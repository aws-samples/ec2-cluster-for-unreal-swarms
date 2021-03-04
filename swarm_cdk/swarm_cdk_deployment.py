## Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
## SPDX-License-Identifier: MIT-0

from aws_cdk import (
    aws_ec2 as ec2,
    aws_iam as iam,
    aws_s3 as s3,
    aws_imagebuilder as imagebuilder,
    aws_autoscaling as autoscaling,
    core    
)

class SwarmDeployment(core.Stack):

    def __init__(self, scope: core.Construct, id: str, bucket, vpc, **kwargs) -> None:
        super().__init__(scope, id, **kwargs)

        publicsubnets = vpc.select_subnets(subnet_type=ec2.SubnetType.PUBLIC)
        privatesubnets = vpc.select_subnets(subnet_type=ec2.SubnetType.PRIVATE)

        # Custom policy to allow writes to S3 bucket for 
        
        writelogspolicy = iam.PolicyStatement(
            effect=iam.Effect.ALLOW,
            actions=["s3:GetObject",],
            resources=[bucket.bucket_arn+ "/*"]
        )

        writelogsdocument = iam.PolicyDocument()
        writelogsdocument.add_statements(writelogspolicy)

        # Instance Role and SSM Managed Policy
        role = iam.Role(self, "SwarmInstanceRole", assumed_by=iam.ServicePrincipal("ec2.amazonaws.com"), inline_policies=[writelogsdocument])

        role.add_managed_policy(iam.ManagedPolicy.from_aws_managed_policy_name("service-role/AmazonEC2RoleforSSM"))
        role.add_managed_policy(iam.ManagedPolicy.from_aws_managed_policy_name("EC2InstanceProfileForImageBuilder"))

        # Create instance profile that EC2 Image builder can use
        # This can be also later used running Swarm instances
        instanceprofile = iam.CfnInstanceProfile(self, "InstanceProfile",
            instance_profile_name="ue4-swarm-instance-profile",
            path="/executionServiceEC2Role/",
            roles=[role.role_name]
        )

        # Security Group for the Swarm instances that allows communication
        securitygroup = ec2.SecurityGroup(self, "SecurityGroup",
            vpc=vpc,
            description="Security Group for UE4 Swarm",
            security_group_name="Allow UE4 Swarm communication",
            allow_all_outbound=True
            )


        # Allow Swarm Agents and Coordinator to talk to each other in same Security Group
        securitygroup.add_ingress_rule(securitygroup, ec2.Port.tcp_range(8008,8009), 'Allow SG Swarm TCP')
        securitygroup.add_ingress_rule(securitygroup, ec2.Port.icmp_ping(), 'Allow SG Swarm ICMP Ping')

        # Parameter for the trusted network
        trusted_cidr = self.node.try_get_context("trusted_cidr")

        # Define default trusted CIDR
        if trusted_cidr is None:
            trusted_cidr = "10.0.0.0/8"

        # Allow RDP, Swarm and ICMP ping from trusted CIDR prefix
        securitygroup.add_ingress_rule(ec2.Peer.ipv4(trusted_cidr), ec2.Port.tcp_range(8008,8009), 'Allow Trusted IP Swarm TCP')
        securitygroup.add_ingress_rule(ec2.Peer.ipv4(trusted_cidr), ec2.Port.icmp_ping(), 'Allow Trusted IP Swarm ICMP Ping')
        securitygroup.add_ingress_rule(ec2.Peer.ipv4(trusted_cidr), ec2.Port.tcp(3389), 'Allow Trusted IP RDP TCP')

        # Read the EC2 Image builder component instructions from a file
        # These instructions are PowerShell commands to download dependencies ZIP.
        # It will then uncompress it, run nessesary installations for the components.

        component_file_path = "assets/component.yml"
        image_builder_instance_type = ["m5.large"]

        with open(component_file_path, 'r') as componentfile:
            componentdata = componentfile.read()

        componentdata = componentdata.replace("S3-BUCKET-NAME", bucket.bucket_name)

        # Define the component for EC2 Image Builder
        swarmcomponent = imagebuilder.CfnComponent(self,
            "SwarmComponent",
            name="Install-Swarm-Dependencies",
            platform="Windows",
            version="1.0.0",
            data=componentdata
        ) 

        # Define the new VPC and Instanceprofile to be used for Image Building
        infraconfig = imagebuilder.CfnInfrastructureConfiguration(self,
            "SwarmInfraConfig",
            name="UE4-Swarm-WindowsServer-2019-Infra-Config",
            instance_profile_name=instanceprofile.instance_profile_name,
            # logging=imagebuilder.CfnInfrastructureConfiguration.S3LogsProperty(s3_bucket_name=bucket.bucket_name),
            subnet_id=privatesubnets.subnets[0].subnet_id,
            security_group_ids=[securitygroup.security_group_id],
            instance_types=image_builder_instance_type
        )

        # Ensure that instance profile has completed creation before applying the aboce config
        infraconfig.add_depends_on(instanceprofile)

        # Lookup latest Windows Server 2019
        basewindows = ec2.MachineImage.latest_windows(ec2.WindowsVersion.WINDOWS_SERVER_2019_ENGLISH_FULL_BASE);

        # Define Image build recipe combinen the Windows image and our Component
        recipe = imagebuilder.CfnImageRecipe(self,
            "ImageRecipe",
            name="UE4-Swarm-Image",
            parent_image=basewindows.get_image(self).image_id,
            version="1.0.0",
            components=[{"componentArn":swarmcomponent.attr_arn}]
        )

        # Start the build of new AMI based on the Recipe and Infra config
        swarmimage = imagebuilder.CfnImage(self,
            "SwarmImage",
            image_recipe_arn=recipe.attr_arn,
            infrastructure_configuration_arn=infraconfig.attr_arn
            )

        # Lookup the AMI ID for the resulting image
        swarmami = ec2.GenericWindowsImage({self.region: swarmimage.attr_image_id})

        # Read the Script to start Swarm Coordinator
        # Will be used in the instance user data
        with open('assets/start-coordinator.ps1', 'r') as coordfile:
            coorduserdata = coordfile.read()

        # Parameter for the instance type
        coordinator_instancy_type = self.node.try_get_context("coordinator_instancy_type")

        # Define default instance type
        if coordinator_instancy_type is None:
            coordinator_instancy_type = "t3.large"

        # Launch the Swarm Coordinator instance
        coordinator = ec2.Instance(self, "ue4-swarm-coordinator",
            instance_type=ec2.InstanceType(coordinator_instancy_type),
            machine_image=swarmami,
            vpc = vpc,
            vpc_subnets = ec2.SubnetSelection(subnet_type=ec2.SubnetType('PRIVATE')),
            role = role,
            security_group=securitygroup,
            user_data=ec2.UserData.custom(coorduserdata)
            )

        with open('assets/start-agent.ps1', 'r') as agentfile:
            agentuserdata = agentfile.read()

        agentuserdata = agentuserdata.replace("COORDINATOR_IP", coordinator.instance_private_ip)

        # Define C: -drives size for the Swarm Agents
        root_device = autoscaling.BlockDevice(
            device_name='/dev/sda1',
            volume=autoscaling.BlockDeviceVolume.ebs(
                volume_size=100,
                delete_on_termination=True
            ),
        )

        # Parameter for the instance type
        agent_instancy_type = self.node.try_get_context("agent_instancy_type")

        # Define default instance type
        if agent_instancy_type is None:
            agent_instancy_type = "t3.xlarge"

        # Create Autoscaling group for Swarm Agents
        # It won't automaticaly scale on load but instead it will automatically
        # Scale down at evening and bring the cluster up again in the morning
        swarmasg = autoscaling.AutoScalingGroup(self, "ue4-swarm-agent",
            instance_type=ec2.InstanceType(agent_instancy_type),
            machine_image=swarmami,
            role=role,
            vpc=vpc,
            vpc_subnets = ec2.SubnetSelection(subnet_type=ec2.SubnetType('PRIVATE')),
            security_group=securitygroup,
            block_devices=[root_device],
            user_data=ec2.UserData.custom(agentuserdata),
            desired_capacity=1,
            max_capacity=1,
            min_capacity=1)


        # Output the AMI ID and Coordinator IP
        output = core.CfnOutput(self, "ImageID",
                                value=swarmimage.attr_image_id,
                                description="Swarm AMI ID")

        output = core.CfnOutput(self, "CoordinatorPrivateIP",
                                value=coordinator.instance_private_ip,
                                description="Coordinator Instance Private IP")

