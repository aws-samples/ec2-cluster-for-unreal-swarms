<powershell>
## Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
## SPDX-License-Identifier: MIT-0

# Define the Swarm Coordinator to start as a Scheduled task at startup
$action = New-ScheduledTaskAction -Execute "C:\ue4-swarm\SwarmCoordinator.exe"
$trigger = New-ScheduledTaskTrigger -AtStartup
$principal = New-ScheduledTaskPrincipal -UserID "NT AUTHORITY\SYSTEM" -LogonType ServiceAccount -RunLevel Highest
Register-ScheduledTask -Action $action -Trigger $trigger -Principal $principal -TaskName "SwarmCoordinator" -Description "UE4 Swarm Coordinator"

# Restart the instance to trigger the Schedule task.
Restart-Computer
</powershell>