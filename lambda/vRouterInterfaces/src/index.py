"""
CloudFormation template transform macro:
"""
from troposphere import Template, Output, Export, Ref, Sub, Tags
import troposphere.ec2 as ec2
import troposphere.ssm as ssm
import boto3
import json
import os
import logging

lambda_client = boto3.client('lambda')
ssm_client = boto3.client('ssm')
ec2_resource = boto3.resource('ec2')


def setup_logging():
    """Logging Function.
    Creates a global log object and sets its level.
    """
    global log
    log = logging.getLogger()
    log_levels = {"INFO": 20, "WARNING": 30, "ERROR": 40}

    # AWS default log format contains timestamp. This becomes redundant in CloudWatch as it already posts the time.
    default_log_format = "%(levelname)s:%(message)s"
    # check for env set logging_format for lambda specific logging needs.
    log_format = os.environ.get('logging_format', default_log_format)

    log_handler = log.handlers[0]
    log_handler.setFormatter(logging.Formatter(log_format))

    if "logging_level" in os.environ:
        log_level = os.environ["logging_level"].upper()
        if log_level in log_levels:
            log.setLevel(log_levels[log_level])
        else:
            log.setLevel(log_levels["ERROR"])
            log.error(
                "The logging_level environment variable is not set to INFO,"
                " WARNING, or ERROR.  The log level is set to ERROR"
            )
    else:
        log.setLevel(log_levels["ERROR"])
        log.warning("The logging_level environment variable is not set. The log level is set to ERROR")
    log.info(f"Logging setup complete - set to log level {log.getEffectiveLevel()}")


def get_parameter_value(name):
    response = ssm_client.get_parameter(Name=name)
    return response.get('Parameter').get('Value')


def subnet_last_ips(*args, **kwargs):
    lambda_arn = get_parameter_value('/automation/GetIps/lambdaArn')

    def get(*args, **kwargs):
        response = lambda_client.invoke(
            FunctionName=lambda_arn,
            InvocationType='RequestResponse',
            Payload=json.dumps(kwargs),
        )
        log.info(response)
        return json.load(response['Payload'])
    return get


def gen_config(ipData, deviceIndex, BGPvRouter, subnetName, subnetDescription, splitBy, vrfName, thisSplit) -> list:
    log.info("Generating Cisco Interface Config...")
    interface_config = (
        f"interface TenGigE0/0/0/{deviceIndex}\n"
        f"  description {subnetName} - {subnetDescription}\n"
        f"  mtu 8400\n"
    )

    if vrfName != "":
        interface_config += f"  vrf {vrfName}\n"

    # Set netmask for Cisco Interface Configuration
    if splitBy > 1:
        netmask = ipData['SubNetworkNetMask']
    else:
        netmask = ipData['NetMask']

    if BGPvRouter == 'yes':
        # Use last IP in subnet as BGPvRouter will only be attached to one vRouter
        if thisSplit == 'no':
            interface_config += f"  ipv4 address {ipData['FloatingIp']} {netmask}\n"
        else:
            interface_config += f"  ipv4 address {ipData['SubNetworkLastIp']} {netmask}\n"
    else:
        interface_config += f"  ipv4 address {ipData['RouterIp']} {netmask}\n"
        interface_config += f"  ipv4 address {ipData['FloatingIp']} {netmask} secondary\n"

    interface_config += f"  no shut\n"
    interface_config += f"!"
    interface_config = interface_config.split("\n")

    return interface_config


def eniRes(t: Template, eniName, subnet, addresses, deviceIndex, securityGroup) -> Template:
    securityGroups = []
    resName = "vRTRInterfaceENI" + deviceIndex
    attachName = "vRTRAttach" + deviceIndex

    if(securityGroup == "default"):
        securityGroups.append(Ref("vRouterSecurityGroup"))
    else:
        securityGroups.append(securityGroup)

    t.add_resource(
        ec2.NetworkInterface(
            resName,
            Description=eniName,
            SubnetId=subnet.subnet_id,
            PrivateIpAddresses=addresses,
            Tags=Tags(
                Name=eniName
            ),
            GroupSet=securityGroups,
            SourceDestCheck=False,
        )
    )

    t.add_resource(
        ec2.NetworkInterfaceAttachment(
            attachName,
            InstanceId=Ref("vRTRInstance"),
            DeviceIndex=deviceIndex,
            NetworkInterfaceId=Ref(resName),
        )
    )
    return t


def addENI(t: Template, subnet, vRouterName, primaryRouter, securityGroup, add_floating_ip) -> Template:
    log.info("Adding new ENI(s) for Subnet: " + subnet.subnet_id)
    # Process tags to Assign relevant tags to variables for use in addENI
    # Tag Key/Value Reference
    # VrouterName: "CS-PE003V-USE1AZ1N001D, CS-PE004V-USE1AZ1N001D"
    # VRouterSubnetSplitBy: 2
    # VrouterInterfacePos: 5
    # BGPvRouter: yes

    subnetName = ""
    subnetDescription = ""
    vrfName = ""
    splitIndex = None
    BGPvRouter = 'no'
    deviceIndex = None
    splitBy = 1
    thisSplit = 'no'

    for tag in subnet.tags:
        if tag["Key"].lower() == 'name':
            subnetName = tag["Value"]
        elif tag["Key"].lower() == 'subnetname':
            subnetDescription = tag["Value"]
        elif tag["Key"].lower() == 'vroutersubnetsplitby':
            splitBy = int(tag["Value"])
        elif tag["Key"].lower() == 'bgpvrouter':
            BGPvRouter = tag["Value"]
        elif tag["Key"].lower() == 'vrouterinterfacepos':
            deviceIndex = tag["Value"]
        elif tag["Key"].lower() == 'vrfname':
            vrfName = tag["Value"]
        elif tag["Key"].lower() == 'vroutersubnetsplitinterfacepos':
            log.info("Subnet: " + subnet.subnet_id + " vroutersubnetsplitinterfacepos: " + tag["Value"])
            splitIndex = tag["Value"]

    if deviceIndex is None:
        log.info("Subnet: " + subnet.subnet_id + " missing tag for vRouterInterfacePos...")
        exit(1)

    # Intialize Resource Name Variable for troposphere resource call
    eniName = "INT-RTR-" + vRouterName + "-" + subnetDescription

    # Retrieve Ip Assignments from GetIps() function
    get_subnet = subnet_last_ips()
    ipData = get_subnet(SubnetId=subnet.subnet_id, PrimaryRouter=primaryRouter)
    # ipData['RouterIp', 'FloatingIp, NetMask,'PrimaryIp','SecondaryIp','DefaultRoute','SubNetworkLastIp','SubNetworkNetMask']

    # Addresses data for Interface IP Assignment
    addresses = []

    if BGPvRouter == 'yes':
        # Use last IP in subnet as BGPvRouter will only be attached to one vRouter
        log.info("BGP Found: " + subnet.subnet_id)
        bgpIPSpec = ec2.PrivateIpAddressSpecification(Primary=True, PrivateIpAddress=ipData['FloatingIp'])
        addresses.append(bgpIPSpec)
    else:
        log.info("Subnet: " + subnet.subnet_id + " missing tag for BGPvRouter Undefined... Defaulted to no")
        # Router Needs to get Primary Address and HA Floating IP address as secondary
        stndIPSpec = ec2.PrivateIpAddressSpecification(Primary=True, PrivateIpAddress=ipData['RouterIp'])
        addresses.append(stndIPSpec)
        if primaryRouter == "True" and add_floating_ip == "true":
            floatingIPSpec = ec2.PrivateIpAddressSpecification(Primary=False, PrivateIpAddress=ipData['FloatingIp'])
            addresses.append(floatingIPSpec)

    newEni = eniRes(t, eniName, subnet, addresses, deviceIndex, securityGroup)
    interface_config = gen_config(ipData, deviceIndex, BGPvRouter, subnetName, subnetDescription, splitBy, vrfName, thisSplit)

    if (splitBy > 1) and splitIndex is not None:
        log.info("Processing Split Subnet: " + subnet.subnet_id)
        splitaddress = []
        thisSplit = 'yes'
        subnetName = ''.join([subnetName, '-lower'])
        subnetDescription = ''.join([subnetDescription, '-lower'])
        eniName = "INT-RTR-" + vRouterName + "-" + subnetDescription
        splitIPSpec = ec2.PrivateIpAddressSpecification(Primary=True, PrivateIpAddress=ipData['SubNetworkLastIp'])
        splitaddress.append(splitIPSpec)
        newEni = eniRes(t, eniName, subnet, splitaddress, splitIndex, securityGroup)
        interface_config.extend(gen_config(ipData, splitIndex, BGPvRouter, subnetName, subnetDescription, splitBy, vrfName, thisSplit))
    elif (splitBy > 1) and splitIndex is None:
        log.info("Subnet: " + subnet.subnet_id + " missing tag for VRouterSubnetSplitInterfacePos...")
        exit(1)
    else:
        log.info("Subnet: " + subnet.subnet_id + " not configured for split..")

    return t, interface_config


def get_vrf_list(target_subnets):
    vrfs = []
    for subnet in target_subnets:
        for tag in subnet.tags:
            # if key vRouterName matches vRouterName need to addENI
            # Assign relevant tags to variables for use in addENI
            if tag['Key'].lower() == 'vrfname':
                if tag['Value'] not in vrfs:
                    vrfs.append(tag['Value'])
    return vrfs


def get_target_subnets(subnets, vRouterName):
    log.info("Get Target Subnets")
    target_subnets = []

    for subnet in subnets:
        vRouters = []
        # log.info("Subnet:")
        # log.info(subnet.subnet_id)
        # log.info(subnet)
        for tag in subnet.tags:
            # if key vRouterName matches vRouterName need to addENI
            # Assign relevant tags to variables for use in addENI
            if tag['Key'].lower() == 'vroutername':
                # log.info("Tag Found...")
                # log.info(tag['Value'])
                # log.info(vRouterName)
                if "," in tag['Value']:
                    # log.info("Split Value Found...")
                    vRouters = tag['Value'].split(",")
                else:
                    vRouters = [tag['Value']]

                # log.info("vRouters")
                # log.info(vRouters)
                if vRouterName in vRouters:
                    target_subnets.append(subnet)
                    # log.info("Subnets Added....")
                    # log.info(subnet)

    return target_subnets


def process_configs(t: Template, fragment, config_list):
    log.info("Processing UserData")
    instance = fragment.get("Resources").get("vRTRInstance")
    userdata = instance.get("Properties").get("UserData").get("Fn::Base64").get("Fn::Sub")
    config_lines = userdata.split("\n")

    interface_config_marker = '>>>interface_config_macro_output<<<'
    config_lines = [
        line
        for line in config_lines
        if line != interface_config_marker
    ]
    if "end" in config_lines[-1]:
        config_lines.pop()

    for config in config_list:
        config_lines.extend(config)

    config_lines.append("end")
    log.info("Here's my new userdata....")
    log.info(config_lines)
    fragment['Resources']['vRTRInstance']['Properties']['UserData']['Fn::Base64']['Fn::Sub'] = '\n'.join(config_lines)

    return t


def process_subnets(t: Template, fragment, subnets, vRouterName, primaryRouter, vrfs, securityGroup, add_floating_ip) -> Template:
    """Scans subnets for ones with vRouterName in subnet Tags"""
    log.info("Processing Subnets")
    config_list = []
    vrf_config = []

    for vrf in vrfs:
        vrf_config.append("vrf " + vrf)
        vrf_config.append("!")

    if len(vrf_config) > 0:
        config_list.append(vrf_config)

    for subnet in subnets:
        t, int_config = addENI(t, subnet, vRouterName, primaryRouter, securityGroup, add_floating_ip)
        config_list.append(int_config)

    t = process_configs(t, fragment, config_list)
    return t


def process_vpc(t: Template, fragment, vRouterName, primaryRouter, vpcId, securityGroup, add_floating_ip):
    vpc = ec2_resource.Vpc(vpcId)
    subnets = list(vpc.subnets.all())
    target_subnets = get_target_subnets(subnets, vRouterName)
    vrfs = get_vrf_list(target_subnets)
    log.info(target_subnets)
    cfn_template = process_subnets(
        t,
        fragment,
        target_subnets,
        vRouterName,
        primaryRouter,
        vrfs,
        securityGroup,
        add_floating_ip
    )
    return cfn_template


def lambda_handler(event, _context):
    """Handle invocation in Lambda (when CloudFormation processes the Macro)"""

    # Enable logging
    setup_logging()

    fragment = event['fragment']
    params = event['templateParameterValues']
    status = "success"

    try:
        log.info(json.dumps(event))
        log.info(json.dumps(fragment))
        vRouterName = params['vRouterName']
        securityGroup = params['SecurityGroupID'].lower()

        log.info("Generating Template for: " + vRouterName)

        if params['vRouterInstance'].lower() == 'primary':
            primaryRouter = "True"
        else:
            primaryRouter = "False"
            
        if params.get('addFloatingIP'):
            add_floating_ip = params['addFloatingIP']
        else:
            add_floating_ip = "true"

        if params.get('VPC'):
            vpcId = params['VPC']
        elif params.get('pVpcDeploymentName'):
            vpcName = params['pVpcDeploymentName']
            vpcId = get_parameter_value(f'/outputs/{vpcName}/vpc/id')
        else:
            log.info("Missing VPC paramater")
            exit(1)

        t = Template()

        cfn_template = process_vpc(t, fragment, vRouterName, primaryRouter, vpcId, securityGroup, add_floating_ip)
        json_template = json.loads(cfn_template.to_json())
        fragment['Resources'].update(json_template['Resources'])

        log.info("Troposphere Generated the following...")
        log.info(json.dumps(json_template))
        log.info("Fragment...")
        log.info(json.dumps(fragment))

    except Exception as e:
        status = "failure"
        log.exception(e)

    return {
        "requestId": event["requestId"],
        "status": status,
        "fragment": fragment,
    }

