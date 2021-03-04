## Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
## SPDX-License-Identifier: MIT-0

from aws_cdk import (
    aws_ec2 as ec2,
    aws_s3 as s3,
    core    
)


class SwarmInfra(core.Stack):

    bucket = s3.IBucket
    vpc = ec2.IVpc

    def __init__(self, scope: core.Construct, id: str, **kwargs) -> None:
        super().__init__(scope, id, **kwargs)

        # Create S3 Bucket for the Swarm Dependencies
        self.bucket = s3.Bucket(self, "ue4-swarm-bucket")

        # Retrieve CIDR from CDK Context for the VPC CIDR
        vpc_cidr = self.node.try_get_context("vpc_cidr")

        # Create new VPC with one subnet for the test EC2 instance
        self.vpc = ec2.Vpc(self, "Swarm-VPC",
            cidr=vpc_cidr,
            nat_gateways=1,
            subnet_configuration=[ec2.SubnetConfiguration(name="Swarm-Public",subnet_type=ec2.SubnetType.PUBLIC),
            ec2.SubnetConfiguration(name="Swarm-Private",subnet_type=ec2.SubnetType.PRIVATE)]
            )

        # Output the S3 Bucket name for the Swarm ZIP file
        output = core.CfnOutput(self, "BucketName",
                                value=self.bucket.bucket_name,
                                description="Swarm Bucket Name")