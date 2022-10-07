"""
CloudFormation template Custom Resource: Returns the last 3 usable IP addresses from the provided subnet-id and default route/netmask
"""
import sys
import boto3
import json
import os
import logging
import urllib3
from netaddr import IPNetwork


http = urllib3.PoolManager()
ec2 = boto3.resource('ec2')

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
            log.error("The logging_level environment variable is not set to INFO, WARNING, or ERROR.  The log level is set to ERROR")
    else:
        log.setLevel(log_levels["ERROR"])
        log.warning("The logging_level environment variable is not set. The log level is set to ERROR")
    log.info(f"Logging setup complete - set to log level {log.getEffectiveLevel()}")


def send(event, context, responseStatus, responseData, physicalResourceId=None, noEcho=False, reason=None):
    """Handles response back to CloudFormation
    Source: https://docs.aws.amazon.com/AWSCloudFormation/latest/UserGuide/cfn-lambda-function-code-cfnresponsemodule.html#w2ab1c27c23c16b9c15
    """

    responseUrl = event['ResponseURL']

    responseBody = {
        'Status' : responseStatus,
        'Reason' : reason or "See the details in CloudWatch Log Stream: {}".format(context.log_stream_name),
        'PhysicalResourceId' : physicalResourceId or context.log_stream_name,
        'StackId' : event['StackId'],
        'RequestId' : event['RequestId'],
        'LogicalResourceId' : event['LogicalResourceId'],
        'NoEcho' : noEcho,
        'Data' : responseData
    }

    json_responseBody = json.dumps(responseBody)

    headers = {
        'content-type' : '',
        'content-length' : str(len(json_responseBody))
    }

    try:
        response = http.request('PUT', responseUrl, headers=headers, body=json_responseBody)
        print("Status code:", response.status)
    except Exception as e:
        print("send(..) failed executing http.request(..):", e)


def get_extra_interfaces(network: IPNetwork, data: dict, parameters):
    """Add 4th to 8th, "last usable" IP addresses to data.
    If ExtraInterfaces in parameters, only return 4th to X last usable IP addresses.
    """
    starting_ip = 6
    end = 11
    extra_interfaces = parameters.get('ExtraInterfaces', 0)
    if  int(extra_interfaces) > 0:
        end = starting_ip + extra_interfaces

    for i in range(starting_ip, end):
        # -2 starts the naming iteration at the number 4 as desired.
        data[f'Secondary{i - 2}'] = str(network[-i + 1])
    return data


def create_ip_data(network: IPNetwork, ipv6=False) -> dict:
    # this splits the subnet into 2 usable subnets
    _split = list(network.subnet(network.prefixlen + 1))
    sub_network = _split[0]
    tmpdict = {
        "FloatingIp": str(network[-2]),
        "PrimaryIp": str(network[-3]),
        "SecondaryIp": str(network[-4]),
        "NetMask": str(network.netmask),
        "DefaultRoute": str(network[1]),
        "SubNetworkLastIp": str(sub_network[-2]),
        "SubNetworkNetMask": str(sub_network.netmask),
    }
    if ipv6:
        tmpdict = {f"{k}V6": v for k, v in tmpdict.items()}
    return tmpdict


def get_last_three(event):
    """Retrun last 3 IPs and default route/netmask of provided subnet from event"""
    if "StackId" in event:
        # this was a call from CloudFormation, reply to CloudFormation
        parameters = event.get('ResourceProperties')
    else:
        parameters = event
    subnet_id = parameters.get('SubnetId')
    subnet = ec2.Subnet(subnet_id)
    subnet_cidr = subnet.cidr_block

    network  = IPNetwork(subnet_cidr)

    data = create_ip_data(network)
    ipv6_networks = subnet.ipv6_cidr_block_association_set
    if len(ipv6_networks) == 1:
        network6 = IPNetwork(ipv6_networks[0].get('Ipv6CidrBlock'))
        data.update(create_ip_data(network6, ipv6=True))
    elif len(ipv6_networks) > 1:
        raise Exception('Only 1 IPv6 CIDR association supported in this code currently.')

    # add extra interfaces based on need.
    data = get_extra_interfaces(network, data, parameters)
    data['az'] = subnet.availability_zone

    # return the corresponding IP addressed based upon what is passed into the Lambda
    if parameters.get('PrimaryRouter').lower() == 'true':
        data['RouterIp'] = data['PrimaryIp']
    else:
        data['RouterIp'] = data['SecondaryIp']
    return data

def lambda_handler(event, context):
    """Handle invocation in Lambda (when CloudFormation builds custom resource)"""

    # Enable logging
    setup_logging()
    log.info(json.dumps(event))

    data = {}

    try:
        # No resources created with this function, do not run when stack is deleted.
        if event.get('RequestType') != "Delete":
            data = get_last_three(event)
        STATUS = 'SUCCESS'
    except Exception as e:
        STATUS = 'FAILED'
        log.exception(e)

    if "StackId" in event:
        # this was a call from CloudFormation, reply to CloudFormation
        send(event, context, STATUS, data)
    else:
        # this could be a call from lambda or other data source
        return data
