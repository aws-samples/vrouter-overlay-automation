# Cisco xRV9k Virtual Router Automation 

This is a solution for deploying Cisco XRV9K virtual routers to be used in an overlay. The solution will provision a single vRouter with dynamic
connectivity to subnets based on subnet tags. The router interface configuration will be injected at initial boot so that interfaces are configured 
to the tagged subnets discovered by a Lambda Function.

# Dependencies:
- An S3 bucket to stage lambda coded
- An existing VPC with appropriate routing configured for SSM reachability
- An SSM bastion host that can access the VPC
- An existing EC2 Keypair for initial root user SSH access
- Access to an appropriate Cisco xRV9K AMI from Marketplace and license: https://aws.amazon.com/marketplace/pp/prodview-ygifeqmzmkqja
- An empty /28 subnet for each vRouter as the GRE Underlay Subnet
- Additional subnets deployed as needed using the below defined tagging model for automatic attachement

# Subnet Tagging Model:
In order for Lambda Automation to dynamically attach to subnets the following subnet tagging model must be used on subnets you wish attached:
- VrouterName: Comma seperated string value of router names i.e "router1,router2" matching instance name tags of vRouters you want to attach to. Typically a pair or just one.
- VrouterInterfacePos: Integer value of ENI Index Posistion.  Value 0 is used by default for the Underlay subnet. Additional subnets should start at 1 follow numerical sequence.
- VRFName: String value name of VRF to be assigned to interfaces on given subnet.
- BGPvRouter: yes/no value primarily affects IP addressing schema to be applied. If BGPvRouter=yes it is assumed only one router is attached to subnet and assigns the last IP to the interface.
	If BGPvRouter=no it is assumed both routers are attaching to same subnet and IP assignments are as follows. Last IP in subnet is assigned as Gateway for clients, 2nd to last assigned
	to first(Primary) router instance and 3rd to last IP assigned to second(Secondary) vrouter instancs as defined by the vRouterInstance cloudformation parameter. 
- subnetName: String value for description of subnets to be applied inside router configuration. 
 
# Key Files:
- cfn/vRouter.yaml - vRouter Instance Cloudformation template 
- cfn/vRouter-Sample-Config.json - Sample parameter file for use with vRouter.yaml
- cfn/vRouter-Security.yaml - vRouter Security Group Cloudformation template for centralized VPC Security Group 
- cfn/vRouter-SecurityGroup-Sample-Confg.json - Sample input file for vRouter-Security.yaml 
- lambda/build.sh - Lambda Build Script
- lambda_deployment.yaml - Cloudformation to deploy lambda functions and required roles
- lambda/GetIps/src/index.py - GetIP function source
- lambda/vRouterInterfaces/src/index.py - vRouterInterfaces function source
- sample_config/* - Sample router tunnel configurations for overlay

# Installation

## Build and stage Lambda functions
1. cd lambda
2. ./build.sh - will build lambda function packages from src
3. aws s3 cp GetIps.zip s3://YOURBUCKETNAMEHERE/
4. aws s3 cp vRouterInterfaces.zip s3://YOURBUCKETNAMEHERE/
5. aws cloudformation create-stack --stack-name vRouter-Lambdas --template-body file://./lambda_deployment.yaml --parameters ParameterKey=pS3LambdaBucket,ParameterValue=YOURBUCKETNAMEHERE --capabilities CAPABILITY_IAM
   * NOTE: Ensure you have pre-created your VPC infrastructure including seperate /28 Underlay subnets for ENI 0 and any additional subnets are created and tagged following the defined tagging model before deploying. 

# Cloudformation Execution 
1. Configure paramters in vRouter-SecurityGroup-Sample-Confg.json for Overlay security group
2. Deploy vRouter-Security.yaml for new environments first to create VPC centralized Security Group ID
   $ aws cloudformation create-stack --template-body file://./vRouter-Security.yaml --parameters file://./vRouter-SecurityGroup-Sample-Confg.json --stack-name vRTR-SG-DEMO
3. Configure parameter file for each vRouter to be used with with vRouter.yaml
4. Deploy vRouter.yaml cloudformation 
   $ aws cloudformation create-stack --capabilities CAPABILITY_AUTO_EXPAND --template-body file://./vRouter.yaml --parameters file://./vRouter-Sample-Config.json --stack-name vRTR-DEMO-001 

# vRouter.yaml Input Parameters:
  * vRouterName - vRouter Hostname should match VrouterName tag on Subnets
  * VPC - ID of VPC vRouter is to be deployed in
  * vRouterInstance - Primary or Secondary value based on wether it is first or second vrouter of a pair. Used for failover IP secondary assignment
  * DataCenterType - r,l,o datacenter types for Region(r), Local Zone(l), Outpost(o)
  * Environment - d,p,t,i,r lifecycle identifier for Dev, Prod, Test, Integration, Replica
  * LoopBackAddress - IPv4 Loop back address from Dish SSOT
  * Loghost - NDC Jump host of environment for logging
  * ISISNetID - ISIS Net ID Configuration formulated from LoopBackAddress provided by Dish SSOT
  * PrefixSidIndex - ISIS Prefix config from DISH SSOT
  * KeyName - EC2 KeyPair name for SSH access
  * SubnetUnderlay - SubnetID for Primary Interface assignment of vRouter
  * ImageID - AMI ImageID to use. There is auto-select logic this over-rides, but you'll need to change the AMI ids to your own as router AMI's are not public
  * SecurityGroupID - Optional VPC Central Security Group to use for all ENI's. If not provided a stand alone group will be created
  * PgName - Optional name of pre-existing placement group 
  * InstanceTypeParam - Instance type to use. Please reference below table for appropriate selection. Modify select list as needed

# Instance Type Selection 

As deploying virtual routers is expected to have multiple interfaces and varying throughput requirements for production deployments, please be sure to
select an instance type that meets your required number of interfaces and speed requirements. Below is a general reference of interface properties for
common instance types. For production deployments it is recommended to disable HyperThreading on the instance deployment. The Cloudformation has
Hyperthreading enabled to accomodate variable instnace types but commented out section for CPUOptions is there if standardizing on a common instance type.

Cisco supported instance types on Marketplace are currently m5.24xlarge, m5n.24xlarge or c4.2xlarge.  

MaxENI | MaxSpeed | Type     
--- | --- | ---
3 | Up to 10 Gigabit | c5.large
4 | Up to 10 Gigabit | c5.xlarge
4 | Up to 10 Gigabit | c5.2xlarge
8 | Up to 10 Gigabit | c5.4xlarge
8 | 10 Gigabit | c5.9xlarge
8 | 12 Gigabit | c5.12xlarge
15 | 25 Gigabit | c5.18xlarge
15 | 25 Gigabit | c5.24xlarge
15 | 25 Gigabit | c5.metal
3 | Up to 25 Gigabit | c5n.large
4 | Up to 25 Gigabit | c5n.xlarge
4 | Up to 25 Gigabit | c5n.2xlarge
8 | Up to 25 Gigabit | c5n.4xlarge
8 | 50 Gigabit | c5n.9xlarge
15 | 100 Gigabit | c5n.18xlarge
15 | 100 Gigabit | c5n.metal
3 | Up to 10 Gigabit | m5.large
4 | Up to 10 Gigabit | m5.xlarge
4 | Up to 10 Gigabit | m5.2xlarge
8 | Up to 10 Gigabit | m5.4xlarge
8 | 10 Gigabit | m5.8xlarge
8 | 10 Gigabit | m5.12xlarge
15 | 20 Gigabit | m5.16xlarge
15 | 25 Gigabit | m5.24xlarge
15 | 25 Gigabit | m5.metal
3 | Up to 25 Gigabit | m5n.large
4 | Up to 25 Gigabit | m5n.xlarge
4 | Up to 25 Gigabit | m5n.2xlarge
8 | Up to 25 Gigabit | m5n.4xlarge
8 | 25 Gigabit | m5n.8xlarge
8 | 50 Gigabit | m5n.12xlarge
15 | 75 Gigabit | m5n.16xlarge
15 | 100 Gigabit | m5n.24xlarge
15 | 100 Gigabit | m5n.metal 

# Configuration
In this section we are providing sample configuration for 2 vRouters. [vRTR-XRv9k-001](sample_config/vRTR-XRv9k-001.txt) and [vRTR-XRv9k-002](sample_config/vRTR-XRv9k-002.txt)

This simple configuration has below configuration

1. Loopback IPs for these 2 vrouters are 172.31.0.1 & 172.31.0.2
2. GRE tunnel IPs between 2 vrouters are 10.0.0.2 & 10.0.0.3. GRE Source for XRv9k-001 is 172.16.0.13 and XRv9k-002 is 172.16.0.30.
3. Route Reflector IP for both vRouter is 172.31.0.5 (Route Reflector configuration not covered)

This configuration is just a sample and can differe by usecase and network design. 

## Security

See [CONTRIBUTING](CONTRIBUTING.md#security-issue-notifications) for more information.

## License

This library is licensed under the MIT-0 License. See the LICENSE file.
