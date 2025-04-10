# Cloud-Based Resume Application

Deploys and tests a resume application with modular AWS CDK.

Currently functional AWS resources:

* CloudFront
* API Gateway
* Lambda
* S3
* IAM policies and roles
* DynamoDB
* SNS topics (email only)
* Cognito User Pool
* Route53
* Certificate Manager
* CloudWatch Dashboards/Alarms
* Lambda-backed custom resources

## Usage

For input into CDK and its post-configuration, environment variables are used. Below are the variables currently used:

AWS CDK:
| Variable | Purpose | Format |
| --- | --- | --- |
| APP\_DEPLOY\_ACCOUNT | AWS account to deploy resources to | AWS 12 digit account number |
| APP\_DEPLOY\_ENV | Which type of environment should be deployed | TEST or PROD |
| APP\_DEV\_BLOG\_URL | URL to place at the bottom of each web page | Standard URL format |
| APP\_DNS\_ZONE\_DOMAIN | The Route53 DNS domain to use | example.com |
| APP\_DNS\_ZONE\_ACCOUNT | The account where the Route53 zone is hosted | AWS 12 digit account number |
| APP\_STACK\_PREFIX | Text to prepend to the CloudFormation stacks | regex: [a-zA-Z0-9-]+ |
| APP\_COGNITO\_LOGIN\_NOTIFICATION\_EMAIL | Email to notify when a user logs in to the management portal | Standard email address format |
| APP\_COGNITO\_INITIAL\_USERNAME | Initial management portal user's username | regex: [a-zA-Z][a-zA-Z0-9.\_-]* |
| APP\_COGNITO\_INITIAL\_USER\_GIVEN\_NAME | Initial management portal user's given name | regex: [a-zA-Z][a-zA-Z]* |
| APP\_COGNITO\_INITIAL\_USER\_EMAIL | Initial management portal user's given name | Standard email address format |
| APP\_COGNITO\_INITIAL\_USER\_PASSWORD | Initial management portal user's password | regex: .+ |
| APP\_MONITORING\_EMAIL\_LIST | A comma separated list of emails for CloudWatch alarm notifications | Standard email address format |
| APP\_HOMEPAGE\_TITLE | Website homepage title | regex: .+ |
| Required in test env only: APP\_TEST\_DNS\_HOST | Host to prepend to all DNS recordsets in test environment | regex: [a-z0-9-]{1,64} |
| Optional: APP\_LAMBDA\_FUNCTION\_INCREMENT | Used to force all lambda functions to update, regardless if their code changed | any string |


Testing:
| Variable | Purpose | Format |
| --- | --- | --- |
| APP\_DEPLOY\_ENV | Which type of environment should be deployed | TEST or PROD |
| APP\_DNS\_ZONE\_DOMAIN | The Route53 DNS domain to use | example.com |
| APP\_DEV\_BLOG\_URL | URL to place at the bottom of each web page | Standard URL format |
| APP\_COGNITO\_INITIAL\_USER\_EMAIL | Initial management portal user's given name | Standard email address format |
| APP\_COGNITO\_INITIAL\_USER\_PASSWORD | Initial management portal user's password | regex: .+ |
| APP\_COGNITO\_INITIAL\_USERNAME | Initial management portal user's username | regex: [a-zA-Z][a-zA-Z0-9.\_-]* |
| APP\_INITIAL\_RESUME\_DOCX\_URL | A URL to initial, ready-to-view docx resume DOCX file to upload | Standard URL format |
| APP\_INITIAL\_RESUME\_SOURCE\_CODE\_URL | A URL to the initial resume's HTML source code to test against | Standard URL format |
| APP\_TEST\_RESUME\_DOCX\_URL | A URL to a sanitized resume file DOCX resume file for testing | Standard URL format |
| APP\_TEST\_RESUME\_SOURCE\_CODE\_URL | A URL to the sanitized resume's HTML source code to test against | Standard URL format |
| APP\_TEST\_MODIFIED\_RESUME\_DOCX\_URL | A URL to a modified version of the test resume DOCX file for testing | Standard URL format |
| APP\_TEST\_MODIFIED\_RESUME\_SOURCE\_CODE\_URL | A URL to the modified version of the test resume's HTML source code to test against | Standard URL format |
| APP\_HOMEPAGE\_SOURCE\_CODE\_URL | A URL to an html file containing the homepage source code to test against | Standard URL format |


This deployment is intended to be configured with JSON files located in src/backend/configuration/config.

| Json File | Associated Resource | Features |
| --- | --- | --- |
| api.json | API Gateway | Rest API, Custom domain with certificate, Stages, Nested resources/methods, Integrations: Lambda, Mock, Token authorizers, Gateway responses, Custom and AWS managed models |
| cdk.json | CDK metadata | Static variables for use in CDK, Replacement strings, CDK custom resource providers |
| certificates.json | Certificate Manager | Certificate domains, Subject alternative names, Associated Route53 DNS resources |
| cloudfront.json | CloudFront | Distributions, Custom certificates, Price class, Logging, Error responses, S3 origin, S3 origin group, default and custom behaviors, CloudFront functions, Lambda@edge functions, custom and AWS managed policies |
| cognito.json | Cognito User Pool | Feature plan, Lambda triggers, Groups, MFA, Password policies, Sign-in Aliases, Standard Attributes, Application clients, Custom domain |
| dns.json | Route53 | For existing zones only: A/AAAA/CNAME/Alias Record sets |
| dynamodb.json | DynamoDB | Tables, Partition keys, Sort keys, Global indexes, Local indexes, Streams, Default items (prepopulate table) |
| iam.json | IAM | Policies, Roles, Resource-Based Policies |
| lambda.json | Lambda | General configuration, IAM Role, AWS Runtimes, Versioning, Aliases, In-code placeholder replacement |
| monitoring.json | CloudWatch Dashboards and Alarms | Dashboards/Alarms for: API Gateway (by API and by method), CloudFront |
| s3.json | S3 | Buckets, Bucket policies, Versioning, Lambda event notifications, CORS, S3 managed encryption, Enforce SSL, Block public access settings |
| sns.json | SNS | Topics, Enforce SSL, Subscriptions, Resource policies |


### Placeholders

Within code of both the project and its CDK definition, placeholders can be used to protect sensitive information or provide runtime values to backend code, frontend code, or CDK resources. These placeholders are in the regex format "\[[A-Z\_0-9]+(ARN|PLACEHOLDER|STRING)\]". Each placeholder must begin and end with square brakets ("[" and "]") and prior to the closing bracket must one one of the following strings: "ARN", "PLACEHOLDER", or "STRING".
Definition of the placeholder values is contained within the JSON file cdk.json. See that section below for more details.


### Deployment order

There is currently a static deployment order of certain resources.

Stack1 - Region: Deployment region
| Resource | Action |
| --- | --- |
| S3 Buckets | Creation + CORS configuration |
| Cognito User Pools | Creation |

Stack2 - Region: us-east-1 - Depends on Stack1
| Resource | Action |
| --- | --- |
| CloudFront | Creation + Configuration |

Stack3 - Region: Deployment region - Depends on Stack2
| Resource | Action |
| --- | --- |
| DynamoDB Tables | Creation + Configuration |
| S3 Buckets | Configuration |
| API Gateway | Creation + Configuration |

Stack4 - Region: us-east-1 - Depends on Stack3
| Resource | Action |
| --- | --- |
| Post-Deployment Custom Resources within region | Creation + Configuration |
| CloudWatch Monitoring | Creation + Configuration |

Stack5 - Region: Deployment Region - Depends on Stack4
| Resource | Action |
| --- | --- |
| Post-Deployment Custom Resources within region | Creation + Configuration |


## DNS Requirements

In order to use SSL certificates and custom domains in CloudFront, API Gateway, and Cognito, a Route53 hosted zone is required when deploying via CDK. In cases when the hosted zone is not in the same account as CDK is deploying to, a role in the hosted zone's account must be created. The role must have a trust policy allowing the deployee account access to the hosted zone. Finally, the deployee account must have a policy that grants the CDK deploy role access to use the hosted zone account's role. Below are the recommended policies and roles.

### Hosed Zone Account

#### IAM policy
```json
{
    "Route53RecordSetAdministrator": {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Sid": "Route53ChangeAndListRecordSets",
                "Effect": "Allow",
                "Action": [
                    "route53:ChangeResourceRecordSets",
                    "route53:ListResourceRecordSets"
                ],
                "Resource": "arn:aws:route53:::hostedzone/[Route53_HOSTED_ZONE_ID]"
            },
            {
                "Sid": "Route53GetChange",
                "Effect": "Allow",
                "Action": [
                    "route53:GetChange"
                ],
                "Resource": "*"
            }
        ]
    }
}
```


#### IAM Role
```json
{
    "Route53RemoteAccessRole": {
        "IAM_Policies": [
            "Route53RecordSetAdministrator"
        ],
        "Trusted_Entities": {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Effect": "Allow",
                    "Principal": {
                        "AWS": "arn:aws:iam::[DEPLOYEE_ACCOUNT]:root"
                    },
                    "Action": "sts:AssumeRole",
                    "Condition": {}
                }
            ]
        }
    }
}
```


### Deployee Account

#### IAM Policy
```json
{
    "Route53RemoteHostedZoneAccess": {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Sid": "STSAssumeRoute53Role",
                "Effect": "Allow",
                "Action": "sts:AssumeRole",
                "Resource": "arn:aws:iam::[HOSTED_ZONE_ACCOUNT]:role/Route53RemoteAccessRole"
            }
        ]
    }
}
```


#### IAM Role
```json
{
    "CDKDeploymentRole": {
        "IAM_Policies": [
            "[OtherCDKPolicies]",
            "Route53RemoteHostedZoneAccess"
        ],
        "Trusted_Entities": {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Effect": "Allow",
                    "Principal": {
                        "AWS": "arn:aws:iam::[DEPLOYEE_ACCOUNT]:root",
                        "AWS": "[other trusted entities]"
                    },
                    "Action": "sts:AssumeRole"
                }
            ]
        }
    }
}
```


## JSON file Format

### General information

The majority of the JSON files follow a standard format for each resource. Any deviations will be noted in the JSON file's section below.
As a general rule, resources have a CDK name denoted as its key. This is usually only used within the CDK synthesis. Exceptions will be denoted in the sections below. The key must only contain upper and lowercase alphanumeric characters and underscores.
All resources requiring CloudFormation logical names will have a key-value pair of "logical\_name": "ResourceLogicalNameInStack" within the resource's definition inside the JSON file. Logical names must consist of only upper and lowercase alphanumeric characters.
Some resources require a duration to be provided. In the JSON files, this is input in an ISO 8601 duration string in the format "P(n)Y(n)M(n)DT(n)H(n)M(n)S"
Keys that require data input will be surrounded by ** on either side.
To reference information across JSON files, placeholders are used. Values that may contain placeholders are denoted with [\_].
All resources have mandatory values, but some values are optional. Mandatory key-value pairs are denoted with (!) in the value. Conditionally mandatory key-value pairs are denoted with (!?) and the conditions will be defined within the value

Note on dependencies. Some resources have a depends\_on key-value pair. This can be used to force a resource to be deployed after another, arbitrary resource. Use this with caution. If a resource is set to depend on a resource in another stack or on a resource which would depend opon it or its parent, a dependency loop will form and cause the deployment to fail.


### CloudFormation Custom Resources

This project implements several CloudFormation custom resources to properly deploy and customize AWS resources. Some custom resources are run post-deployment while others are run in-line. Below is a listing of all custom resources and what they may be used with.


#### Custom::ACMCertificateCreatorValidator

ACMCertificateCreatorValidator is used during runtime to create, validate, and delete Certificate Manager certificates. It requires a Route53 hosted zone and works in tandem with the custom resource Custom::CreateDNSRecords. It requires the same role that Custom::CreateDNSRecords to create certificate validation records in the hosted zone.

Implemented in: certificates.json -> [certificate] -> custom_resources
Implementation:
```json
{
    "...": "...",
    "custom_resources": {
        "**arbitrary name**": {
            "logical_name": "CustomResourceLogicalName",
            "resource_type": "Custom::ACMCertificateCreatorValidator",
            "provider": "cfn-provider-create-and-validate-acm-certificates",
            "depends_on": []
        }
    }
}
```


#### Custom::ApiGatewayIntegrationUpdater
ApiGatewayIntegrationUpdater is used post-deployment to update integration endpoints inside an API gateway REST API method. It's intended to be used when a lambda function changes and generates a new version post-deployment.

Implemented in: api.json -> API -> resources -> ... -> methods -> [method]-> integration\_request -> post\_deployment\_custom\_resources
Implementation:
```json
{
    "...": "...",
    "**arbitrary name**": {
        "logical_name": "string (!) - CustomResourceLogicalName",
        "resource_type": "Custom::ApiGatewayIntegrationUpdater",
        "provider": "cfn-provider-update-api-gateway-lambda-integration-versions",
        "depends_on": [
            "string - LambdaCustomResourceUpdaterLogicalName"
        ]
    }
}
```


#### Custom::CloudFrontBehaviorEdgeLambdaUpdater
CloudFrontBehaviorEdgeLambdaUpdater is used post-deployment to update the version a CloudFront distribution's behavior(s) are pointing at. It's intended to be used when a lambda function changes and generates a new version post-deployment.

Implemented in: cloudfront.json -> distributions -> [distribution] -> default\_behavior/additional\_behaviors -> functions -> [function type] -> post\_deployment\_custom\_resources
Implementation:
```json
{
    "...": "...",
    "post_deployment_custom_resources": {
        "**arbitrary name**": {
            "logical_name": "string (!) - CustomResourceLogicalName",
            "resource_type": "Custom::CloudFrontBehaviorEdgeLambdaUpdater",
            "provider": "cfn-provider-update-cloudfront-behavior-edge-lambda-version",
            "is_default_cache_behavior": "boolean (!) - must be true when used in default behavior",
            "depends_on": [
                "string - LambdaFunctionCustomResourceLogicalName"
            ]
        }
    }
}
```


#### Custom::CloudFrontFunctionPlaceholderReplacer
CloudFrontFunctionPlaceholderReplacer is used post-deployment to replace placeholders in a CloudFront function. This is especially useful when the URL of a deployment is variable, such as in test vs prod environments.

Implemented in: cloudfront.json -> cloudfront\_functions -> [function] -> post\_deployment\_custom\_resources
Implementation:
```json
{
    "...": "...",
    "post_deployment_custom_resources": {
        "**arbitrary  name**": {
            "logical_name": "string (!) - CustomResourceLogicalName",
            "resource_type": "Custom::CloudFrontFunctionPlaceholderReplacer",
            "provider": "cfn-provider-cloudfront-function-redirect-placeholders",
            "domain_name": "string (!)[_]",
            "domain_uri": "string (!)[_]"
        }
    }
}
```


#### Custom::CreateDNSRecords

CreateDNSRecords is used during runtime to create recordsets within a Route53 hosted domain. If the hosted domain belongs to a different account than CDK is deploying to, that account must have a role associated with it granting the deployee account access. The role ARN may be provided directly or via a placeholder in the custom resource implementation.

Implemented in: dns.json -> record\_sets -> [record set] -> custom\_resources
Implementation:
```json
{
    "...": "...",
    "custom_resources": {
        "**arbitrary name**": {
            "logical_name": "string (!) - CustomResourceLogicalName",
            "resource_type": "Custom::CreateDNSRecords",
            "provider": "cfn-provider-create-dns-records",
            "depends_on": [
                "string - RecordSetLogicalName"
            ]
        }
    }
}
```


#### Custom::EmptyBucket
EmptyBucket is used post-deployment, or rather, pre-destroy to empty an S3 bucket of all objects before deletion.

Implemented in: s3.json -> [bucket] -> post\_deployment\_custom\_resources
Implementation:
```json
{
    "...": "...",
    "post_deployment_custom_resources": {
        "**arbitrary name**": {
            "logical_name": "string (!) - CustomResourceLogicalName",
            "resource_type": "Custom::EmptyBucket",
            "provider": "cfn-provider-empty-bucket",
            "empty_on_prod": "boolean (!)",
            "depends_on": [
                "string - BucketLogicalName"
            ]
        }
    }
}
```


#### Custom::LambdaPlaceholderReplacer
LambdaPlaceholderReplacer is used post-deployment to replace placeholders in a lambda function. It's most common use case is when a lambda function needs specific information about other resources deployed with it in the same CDK application.

Implemented in: lambda.json -> [lambda function] -> post\_deployment\_custom\_resources
Implementation:
```json
{
    "...": "...",
    "post_deployment_custom_resources": {
        "**arbitrary name**": {
            "logical_name": "string (!) - CustomResourceLogicalName",
            "resource_type": "Custom::LambdaPlaceholderReplacer",
            "provider": "cfn-provider-update-lambda-function-placeholders",
            "files_with_placeholders": [
                "string (!) - relative path to file containing placeholders"
            ],
            "depends_on": [
                "string - LambdaFunctionLogicalName"
            ]
        }
    }
}
```


### api.json
```json
{
    "APIName": {
        "logical_name": "string (!)",
        "configuration": {
            "custom_domains": [
                {
                    "logical_name": "string (!) - NOTE: CUSTOM DOMAINS ARE OPTIONAL",
                    "domain_host": "string (!)[_] - Host, including domain, for the API",
                    "stage": "string (!) - The stage name of the API",
                    "path": "string (!) - The path, including the stage, in the stage the custom domain should use, with no leading slash",
                    "certificate": "string (!) - must reference a resource key in certificates.json",
                    "dns_recordset": "string (!) - must reference a resource key in dns.jso)",
                    "depends_on": [
                        "string - ResourceLogicalName - recommend depending on the custom resource certificate creator used to create the custom domain's certificates"
                    ]
                }
            ],
            "description": "string (!)",
            "enable_default_cloudwatch_role": "boolean (!)",
            "endpoint_type": "string - See CDK API docs aws_apigateway.EndpointType keys for valid options",
            "endpoint_export_name": "string (!)",
            "retain_default_cloudwatch_role": "boolean - Whether the API gateway's CloudWatch role should be retained upon the gateway's deletion - Default: destroy",
            "stages": {
                "**StagePhysicalName**": {
                    "logical_name": "string (!)",
                    "deployment": {
                        "logical_name": "string (!) - Which deployment the stage should belong to - NOTE: This deployment section is optional",
                        "retain_deployments": "boolean"
                    },
                    "cache_data_encrypted": "boolean - Default: false",
                    "caching_enabled": "boolean - Default: false",
                    "cache_ttl": "string - Duration format",
                    "metrics_enabled": "boolean - Default: false",
                    "logging_level": "string - See CDK API docs aws_apigateway.MethodLoggingLevel keys for valid options",
                    "throttling_burst_limit": "int/float",
                    "throttling_rate_limit": "int/float,
                    "tracing_enabled": "boolean - Default: false",
                    "stage_variables": {
                        "**VariableName**": "string - Must be of format [A-Za-z0-9-._~:/?#&=,]+ - NOTE: stage_variables key is optional"
                    }
                }
            }
        },
        "resources": {
            "**ResourcePhysicalName**" : {
                "resources": {
                    "**RecursiveResources**": {
                        "resources": {},
                        "methods": {}
                    }
                },
                "methods": {
                    "**DELETE/GET/HEAD/OPTIONS/POST/PUT/PATCH**": {
                        "method_request": {
                            "body_validation": {
                                "**ContentType (e.g. application/json)**": "string (!) - must reference a model key from same API definition"
                            },
                            "authorization": "string - must reference an authorizer key from same API definition",
                            "authorization_type": "string (!?) - Note: Currently CUSTOM is the only valid option - Mandatory only if authorization is set",
                            "request_validator": {
                                "validate_body": "boolean - Default: false",
                                "validate_parameters": "boolean - Default: false"
                            }
                        },
                        "integration_request": {
                            "integration_type": "string (!) - Valid types are currently lambda and mock",
                            "lambda_proxy": "boolean (!?) - Mandatory if integration type is lambda",
                            "request_templates": {
                                "**ContentType (e.g. application/json)**": "string (!?) - Mandatory if lambda_proxy is false or if integration type is mock - Format: JSON string dump of a mapping template. For more information, see https://docs.aws.amazon.com/apigateway/latest/developerguide/api-gateway-mapping-template-reference.html"
                            },
                            "lambda_function": {
                                "name": "string (!) - must reference a lambda function key in lambda.json - Note: lambda_function is required only if integration_type is lambda",
                                "alias": "string (!) - physical name of function alias - Note: if provided, alias name must match the alias in the lambda function's definition in lambda.json"
                            },
                            "post_deployment_custom_resources": {
                                "**arbitrary custom resource-unique name - Note: post_deployment_custom_resources section is not mandatory**": {
                                    "logical_name": "string (!)",
                                    "resource_type": "string (!) - Currently for API Gateway custom resources, the only valid option is Custom::ApiGatewayIntegrationUpdater",
                                    "provider": "string (!) - must reference a provider key defined in cdk.json",
                                    "depends_on": [
                                        "string - ResourceLogicalName - recommeded to depend on the custom resource that updates the method's integration"
                                    ]
                                }
                            }
                        },
                        "integration_response": {
                            "responses": [
                                {
                                    "status_code": "string (!) - Must be a valid HTTP status code - Note: integration_response is mandatory if integration request type is not lambda proxy",
                                    "header_mappings": [
                                        {
                                            "name": "string (!) - path of header, such as method.response.header.Specific-Header",
                                            "value": "string (!)[_] - must be surrounded by single quotes"
                                        }
                                    ]
                                }
                            ]
                        },
                        "method_response": {
                            "responses": [
                                {
                                    "status_code": "string (!) - Must be a valid HTTP status code - Note: responses is not mandatory for lambda proxy integrations, however if not provided, the lambda function must return a value of the correct format to the client",
                                    "headers": [
                                        "string (!)",
                                    ],
                                    "body": {
                                        "models": {
                                            "**ContentType (e.g. application/json)**": "string (!) - Must be one of: EMPTY (AWS empty model), ERROR (AWS error model), or reference a model key defined in same API definition"
                                        }
                                    }
                                }
                            ]
                        }
                    }
                }
            },
            "**...**": {
                "...": {}
            }
        },
        "authorizers": {
            "**AuthorizerPhysicalName**": {
                "logical_name": "string (!)",
                "authorizer_type": "string - Note: Currently the only valid option is TOKEN",
                "token_source": "string (!) - Header name where token will be located",
                "token_validation": "string (!) - javascript-style regex",
                "lambda_function": "string (!) - must reference a lambda function key in lambda.json",
                "function_alias": "string - physical name of function alias - Note: if provided, alias name must match the alias in the lambda function's definition in lambda.json"
            }
        },
        "gateway_responses": {
            "**NameOfResponse - See CDK API docs aws_apigateway.ResponseType keys for valid options**": {
                "logical_name": "string (!) - Note: Logical name must be unique only within the same API definition. Different definitions may have the same logical name",
                "status_code": "string - must be a valid HTTP status code - Default: standard HTTP status code for response type",
                "headers": {
                    "**HeaderName**": "string (!) - must be surrounded by single quotes"
                },
                "templates": {
                        "**ContentType (e.g. application/json)**": "string (!) - format: JSON string dump of a mapping template. For more information, see https://docs.aws.amazon.com/apigateway/latest/developerguide/api-gateway-mapping-template-reference.html"
                }
            }
        },
        "models": {
            "**ModelPhysicalName**": {
                "logical_name": "string (!)",
                "content_type": "string (!) - must be a valid content type (e.g. application/json)",
                "model": {
                    "title": "NOTE: model must be a valid JSON schema object"
                }
            },
        }
    }
}
```


### cdk.json

#### Placeholder definition
Within cdk.json, placeholder definitions are defined. Placeholder names may contain uppercase alphanumeric characters and underscores and are surrounded by brackets. They may end in one of three ways: STRING, PLACEHOLDER, and ARN. The ending is for human-readability purposes and does not serve a technical function. The regex for a placeholder is "\[[A-Z\_0-9]+(ARN|PLACEHOLDER|STRING)\]". Each placeholder definition allows for different values in prod and test environments, or the same value for both. It can also be set to export its value into stack output. The definition may come from different types of sources. Each placeholder definition will consist of an initial source and can have a list of both prefix sources and suffix sources. Sources also have keys that accompany them. Below is a table of the sources:

| Source | Definition | Keys | Values |
| --- | --- | --- | --- |
| env | environment variable | variable\_name | Name of environment variable |
| cdk\_cfn\_variable\_replacements | other placeholder values | variable\_name | Exact name of placeholder including brackets |
| provided | literal string | value | string |
| cdk\_static\_variables | static variables defined in the cdk.json's static\_variables section | variable\_name | Name of variable in static_variables |
| resource\_attribute | an attribute of a CDK resource | resource\_logical\_name; attribute | Logical name of resource; Resource attribute name as defined in the CDK API docs |
| config_file | a string, number, or boolean in a config file - useful for storing a setting in the stack outputs | file; path | the config file; a path (string if sub-object is dict or integer if sub-object is a list) to the data |

As an example, if there were several separate pieces of data to be included in a placeholder value, such as a url, it may look something like this:

```json
{
    "[IMPORTANT_URL_PLACEHOLDER]": {
        "environments": {
            "ALL": {
                "source": "env",
                "variable_name": "COMPANY_DNS_NAME",
                "prefixes": [
                    {
                        "source": "provided",
                        "value": "https://www."
                    },
                    {
                        "source": "cdk_static_variables",
                        "variable_name": "company_host"
                    },
                    {
                        "source": "provided",
                        "value": "."
                    }
                ],
                "suffixes": [
                    {
                        "source": "provided",
                        "value": ".com/index.html?clientid="
                    },
                    {
                        "source": "resource_attribute",
                        "resource_logical_name": "SomeResourceLogicalName",
                        "attribute": "client_id"
                    },
                    {
                        "source": "provided",
                        "value": "&redirect="
                    },
                    {
                        "source": "cdk_cfn_variable_replacements",
                        "variable_name": "[REDIRECT_URL_PART_STRING]",
                    }
                ]
            }
        },
        "export": true
    }
}
```

The above will take the environment variable value of COMPANY\_DNS\_NAME, prepend it with "https://www." + static\_variables["company\_host"] + ".", then append it with ".com/index.html?clientid=" + SomeResourceLogicalName.client\_id + "&redirect=" + cdk\_cfn\_variable\_replacements["[REDIRECT\_URL\_PART\_STRING]"]. It would then form something such as "https://www.host.company.com/index.html?clientid=SomeClientId&redirect=https://some.url.com". This value would then be exported and made available in stack output.


```json
{
    "static_variables": {
        "**variable_name**": "string - Used in cfn_variable_replacements below"
    },
    "cfn_variable_replacements": {
        "**[SOME_PLACEHOLDER]**": {
            "environments": {
                "**PROD/TEST/ALL - Note: if ALL is used, neither PROD nor TEST may accompany it**": {
                    "source": "..."
                },
                "**PROD/TEST**": {

                }
            },
            "export": "boolean (!)"
        }
    },
    "custom_resources": {
        "providers": {
            "**name-of-provider**": {
                "logical_name": "string (!)",
                "on_event_handler": "string (!) - Handler to call the custom function - must reference a lambda function key in lambda.json",
                "is_complete_handler": "string - Handler for the custom resource to call to check if the custom resource's actions have been completed - must reference a lambda function key in lambda.json",
                "total_timeout": "string - Format: duration up to 45 minutes",
                "resources_iam_policies": [
                    {
                        "name": "string (!) - must reference an IAM policy key in iam.json - Note: resources_iam_policies is not required, but provides the ability to grant the custom resource access to dynamically named resources",
                        "service": "string (!) - the name of the service being granted access - Note: Currently the only services implemented are lambda and apigateway",
                        "statement_number": "int (!) - The statement of the IAM policy that the resources should be added to - Note: The statement's Resource value must be blank"
                    }
                ]
            }
        }
    }
}
```

### certificates.json
```json
{
    "**ArbitraryCertificateName**": {
        "logical_name": "string (!)",
        "certificate_domain": "string (!)[_]",
        "subject_alternative_names": [
            "string [_]"
        ],,
        "certificate_region": "string (!)[_]",
        "retain_certificate_on_in_use_failure": "boolean (!) - Whether destroying the stack should fail if a certificate is still in use by a resource",
        "dns_zone_domain": "string (!)[_]",
        "dns_zone_role_arn": "string [_] - See Additional Requirements section for more information on this role",
        "route53_hosted_zone_id": "string (!)[_]",
        "custom_resources": {
            "**arbitrary name**": {
                "logical_name": "string (!) - globally unique custom resource logical ID - NOTE: This custom resource is required as it is what creates, validates, and destroys certificates",
                "resource_type": "string (!) - must be Custom::ACMCertificateCreatorValidator",
                "provider": "string (!) - must be cfn-provider-create-and-validate-acm-certificates",
                "depends_on": [
                    "string - ResourceLogicalName"
                ]
            }
        }
    }
}
```


### cloudfront.json
```json
{
    "distributions": {
        "**arbitrary-distribution-name**": {
            "logical_name": "string (!)",
            "general": {
                "cnames": [
                    "string (!)[_]"
                ],
                "certificate": "string (!) - must reference a certificate key within certificates.json",
                "comment": "string",
                "default_root_object": "string (!)",
                "enabled": "boolean - Default: true",
                "enable_ipv6": "boolean - Default: false",
                "http_version": "string - See CDK API docs aws_cloudfront.HttpVersion keys for valid options",
                "minimum_protocol_version": "string - See CDK API docs aws_cloudfront.SecurityPolicyProtocol keys for valid options",
                "price_class": "string",
                "publish_additional_metrics": "boolean",
                "dns_recordset": "string (!) - must reference a recordset key within dns.json",
                "depends_on": [
                    "string - ResourceLogicalName"
                ]
            },
            "logging": {
                "enable_logging": "boolean - default false",
                "log_bucket": "string - must reference an S3 bucket key within s3.json",
                "log_file_prefix": "string",
                "log_includes_cookies": "string"
            },
            "error_responses": [
                {
                    "http_status": "int (!) - must be a valid HTTP status code - Note: error_responses is not a mandatory section",
                    "response_http_status": "int (!) - must be a valid HTTP status code",
                    "response_page_path": "string",
                    "ttl": "string - format: duration"
                }
            ],
            "origins": {
                "**arbitrary-origin-name**" : {
                    "origin_type": "string (!) - currently the only origin type implemented is S3BucketOriginWithOAC",
                    "bucket_name": "resume-static-webpage",
                    "origin_path": "/"
                }
            },
            "origin_groups": {
                "**arbitrary-origin-group-name**": {
                    "primary_origin": "string (!) - must reference an origin key within the cloudfront.json file - Note: origin_groups is not a mandatory section",
                    "fallback_origin": "string (!) - must reference an origin key within the cloudfront.json file",
                    "fallback_status_codes": [
                        "int (!) - must be a valid HTTP status code"
                    ]
                }
            },
            "default_behavior": {
                "origin": {
                    "origin_group": "boolean - Whether the origin is an origin group or a standard group",
                    "name": "string (!) - must reference an origin key within the cloudfront.json file"
                },
                "viewer_protocol_policy": "string - See CDK API docs aws_cloudfront.ViewerProtocolPolicy keys for valid options",
                "allowed_http_methods": "string - See CDK API docs aws_cloudfront.AllowedMethods keys for valid options",
                "cache_policy": {
                    "aws_managed": "boolean - Note: cache_policy is not a mandatory section",
                    "name": "string (!) - must either reference a cache_policy key within the cloudfront.json file or reference an AWS managed cache policy if aws_managed is true - if aws_managed is true, see CDK API docs aws_cloudfront.CachePolicy keys for valid options"
                },
                "origin_request_policy": {
                    "aws_managed": "boolean - Note: origin_request_policy is not a mandatory section",
                    "name": "string (!) - must either reference an origin_request_policy key within the cloudfront.json file or reference an AWS managed origin request policy if aws_managed is true - if aws_managed is true, see CDK API docs aws_cloudfront.OriginRequestPolicy keys for valid options"
                },
                "response_headers_policy": {
                    "aws_managed": "boolean - Note: response_headers_policy is not a mandatory section",
                    "name": "string (!) - must either reference an response_headers_policy key within the cloudfront.json file or reference an AWS managed response headers policy if aws_managed is true - if aws_managed is true, see CDK API docs aws_cloudfront.ResponseHeadersPolicy keys for valid options"
                },
                "functions": {
                    "**event_type - (cloudfront function example) - See CDK API docs aws_cloudfront.FunctionAssociation keys for valid options**": {
                        "type": "string (!) - for a cloudfront function: cloudfront - Note: functions is not a mandatory section",
                        "function_name": "string (!) - function's physical name - must reference a cloudfront function key within the cloudfront.json file ",
                        "include_body": "boolean - Default: false - whether to include the body of the request"
                    },
                    "**event_type - (edge lambda example) - See CDK API docs aws_cloudfront.FunctionAssociation keys for valid options**": {
                        "type": "string - for an edge lambda function: EdgeLambda - Note: functions is not a mandatory section",
                        "function_name": "string (!) - must reference a lambda function key in lambda.json",
                        "version": "string (!) - the specific function version to use - to use the latest function: LATEST",
                        "include_body": "boolean (!) - Default: false - whether to include the body of the request",
                        "post_deployment_custom_resources": {
                            "**arbitrary custom resource-unique name - this custom resource is mandatory only if the referenced lambda function has custom resources**": {
                                "logical_name": "string (!)",
                                "resource_type": "string (!) - Currently for edge lambda custom resources, the only valid option is Custom::CloudFrontBehaviorEdgeLambdaUpdater",
                                "provider": "string (!) - must reference a provider key defined in cdk.json",
                                "is_default_cache_behavior": "boolean (!) - must be true when used in default behavior",
                                "depends_on": [
                                    "string - ResourceLogicalName - Recommend adding dependencies of any custom resources the referenced lambda function has"
                                ]
                            }
                        }
                    }
                }
            },
            "additional_behaviors": [
                {
                    "origin": {
                        "origin_group": "boolean - Whether the origin is an origin group or a standard group",
                        "name": "string (!) - must reference an origin key within the cloudfront.json file"
                    },
                    "path_pattern": "string (!) - simple single-glob path to match with leading slash",
                    "viewer_protocol_policy": "string - See CDK API docs aws_cloudfront.ViewerProtocolPolicy keys for valid options",
                    "allowed_http_methods": "string - See CDK API docs aws_cloudfront.AllowedMethods keys for valid options",
                    "cache_policy": {
                        "aws_managed": "boolean - Note: cache_policy is not a mandatory section",
                        "name": "string (!) - must either reference a cache_policy key within the cloudfront.json file or reference an AWS managed cache policy if aws_managed is true - if aws_managed is true, see CDK API docs aws_cloudfront.CachePolicy keys for valid options"
                    },
                    "origin_request_policy": {
                        "aws_managed": "boolean - Note: origin_request_policy is not a mandatory section",
                        "name": "string (!) - must either reference an origin_request_policy key within the cloudfront.json file or reference an AWS managed origin request policy if aws_managed is true - if aws_managed is true, see CDK API docs aws_cloudfront.OriginRequestPolicy keys for valid options"
                    },
                    "response_headers_policy": {
                        "aws_managed": "boolean - Note: response_headers_policy is not a mandatory section",
                        "name": "string (!) - must either reference an response_headers_policy key within the cloudfront.json file or reference an AWS managed response headers policy if aws_managed is true - if aws_managed is true, see CDK API docs aws_cloudfront.ResponseHeadersPolicy keys for valid options"
                    },
                    "functions": {
                        "**see default behavior functions examples**": {}
                    }
                }
            ]
        }
    },
    "policies": {
        "cache": {
            "**physical policy name**": {
                "logical_name": "string (!)",
                "comment": "string",
                "cookie_behavior": {
                    "type": "string (!) - Note: cookie_behavior section is not mandatory - See CDK API docs aws_cloudfront.CacheCookieBehavior keys for valid options",
                    "cookies": [
                        "string (!) - cookies section is only mandatory if type is set to allow_list"
                    ]
                },
                "enable_accept_encoding_brotli": "boolean",
                "enable_accept_encoding_gzip": "boolean",
                "min_ttl": "string - format: duration",
                "default_ttl": "string - format: duration",
                "max_ttl": "string - format: duration",
                "header_behavior": {
                    "type": "string (!) - Note: header_behavior section is not mandatory - See CDK API docs aws_cloudfront.CacheHeaderBehavior keys for valid options",
                    "headers": [
                        "string (!) - headers section is only mandatory if type is set to allow_list"
                    ]
                },
                "query_string_behavior": {
                    "type": "string (!) - Note: query_string_behavior section is not mandatory - See CDK API docs aws_cloudfront.CacheQueryStringBehavior keys for valid options",
                    "query_strings": [
                        "string (!) - query_strings section is only mandatory if type is set to allow_list"
                    ]
                }
            }
        },
        "origin_request": {
            "**physical policy name**": {
                "logical_name": "string (!)",
                "comment": "string",
                "cookie_behavior": {
                    "type": "string (!) - Note: cookie_behavior section is not mandatory - See CDK API docs aws_cloudfront.OriginRequestCookieBehavior keys for valid options",
                    "cookies": [
                        "string (!) - cookies section is only mandatory if type is set to allow_list"
                    ]
                },
                "header_behavior": {
                    "type": "string (!) - Note: header_behavior section is not mandatory - See CDK API docs aws_cloudfront.OriginRequestHeaderBehavior keys for valid options",
                    "headers": [
                        "string (!) - headers section is only mandatory if type is set to allow_list"
                    ]
                },
                "query_string_behavior": {
                    "type": "string (!) - Note: query_string_behavior section is not mandatory - See CDK API docs aws_cloudfront.OriginRequestQueryStringBehavior keys for valid options",
                    "query_strings": [
                        "string (!) - query_strings section is only mandatory if type is set to allow_list"
                    ]
                }
            }
        },
        "response_header": {
            "**physical policy name**": {
                "logical_name": "string (!)",
                "comment": "string",
                "cors_behavior": {
                    "access_control_allow_credentials": "boolean - Note: cors_behavior section is not mandatory",
                    "access_control_allow_headers": [
                        "string - Note: access_control_allow_headers section is not mandatory"
                    ],
                    "access_control_allow_methods": [
                        "string - Note: access_control_allow_methods section is not mandatory"
                    ],
                    "access_control_allow_origins": [
                        "string - Note: access_control_allow_origins section is not mandatory"
                    ],
                    "origin_override": "boolean",
                    "access_control_expose_headers": [
                        "string - Note: access_control_expose_headers section is not mandatory"
                    ]
                    "access_control_max_age": "string - format: duration"
                }
                "custom_headers": [
                    {
                        "header": "string (!) - header name - Note: custom_headers section is not mandatory",
                        "value": "string (!)",
                        "override": "boolean (!) - whether Cloudfront headers should override the custom header"
                    }
                ],
                "remove_headers": [
                    "string - Note: remove_headers section is not mandatory"
                ],
                "security_headers_behavior": {
                    "content_security_policy": {
                        "policy": "string - Note: neither security_headers_behavior nor content_security_policy sections is not mandatory",
                        "override": "boolean"
                    },
                    "content_type_options": {
                        "override": "boolean - Note: content_type_options section is not mandatory"
                    },
                    "frame_options": {
                        "frame_option": "string - Note: frame_options section is not mandatory - See CDK API docs aws_cloudfront.HeadersFrameOption keys for valid options",
                        "override": "boolean"
                    },
                    "referrer_policy": {
                        "policy": "string - Note: referrer_policy section is not mandatory - See CDK API docs aws_cloudfront.HeadersReferrerPolicy keys for valid options",
                        "override": "boolean"
                    },
                    "strict_transport_security": {
                        "access_control_max_age": "string - format: duration - Note: strict_transport_security section is not mandatory",
                        "include_subdomains": "boolean",
                        "preload": "boolean",
                        "override": "boolean"
                    },
                    "xss_protection": {
                        "protection": "boolean - Note: xss_protection section is not mandatory",
                        "mode_block": "boolean",
                        "report_uri": "string",
                        "override": "boolean"
                    }
                },
                "server_timing_sampling_rate": "int/float"
            }
        }
    },
    "cloudfront_functions": {
        "**cloudfront function physical name**": {
            "logical_name": "string (!)",
            "code_location": "string (!) - relative location of code within the repository without leading slash",
            "auto_publish": "boolean (!)",
            "post_deployment_custom_resources": {
                "**arbitrary custom resource-unique name**": {
                    "logical_name": "string (!) - Note: post_deployment_custom_resources section is not mandatory",
                    "resource_type": "string (!) - currently the only valid option is Custom::CloudFrontFunctionPlaceholderReplacer",
                    "provider": "string (!) - must reference a provider key defined in cdk.json",
                    "domain_name": "string (!)[_]",
                    "domain_uri": "string (!)[_]"
                }
            }
        }
    }
}
```


### cognito.json
```json
{
    "userpools": {
        "**arbitrary userpool name**": {
            "logical_name": "string (!)",
            "name": "string (!) - physical name of user pool",
            "feature_plan": "string (!) - See CDK API docs aws_cognito.FeaturePlan keys for valid options",
            "email": {
                "type": "string (!) - currently the only valid option is cognito - Note: email section is not mandatory",
                "reply_to": "string (!) - valid email address"
            },
            "lambda_triggers": {
                "**user-pool-trigger-name - See CDK API docs aws_cognito.UserPoolTriggers keys for valid options**": {
                    "name": "string (!) - must reference a lambda function key in lambda.json - Note: lambda_triggers section is not mandatory",
                    "sns_topic": "string (!) - must reference an SNS topic key in sns.json"
                }
            },
            "groups": [
                {
                    "group_names": "string (!)[_]",
                    "description": "string (!)"
                }
            ],
            "authentication": {
                "account_recovery": "string (!) - See CDK API docs aws_cognito.AccountRecovery keys for valid options",
                "auto_verify": [
                    "string - See CDK API docs aws_cognito.AutoVerifiedAttrs keys for valid options - Note: auto_verify section is not mandatory"
                ],
                "mfa": "string - See CDK API docs aws_cognito.Mfa keys for valid options",
                "mfa_second_factor": [
                    "string - See CDK API docs aws_cognito.MfaSecondFactor keys for valid options - Note: mfa_second_factor section is not mandatory"
                ],
                "password_policy": {
                    "min_length": "int - Note: password_policy section is not mandatory",
                    "require_digits": "boolean",
                    "require_lowercase": "boolean",
                    "require_symbols": "boolean",
                    "require_uppercase": "boolean"
                },
                "self_sign_up_enabled": "boolean",
                "sign_in_aliases": {
                    "username": "boolean - Note: sign_in_aliases section is not mandatory",
                    "email": "boolean"
                },
                "sign_in_case_sensitive": "boolean",
                "standard_attributes": {
                    "**attribute_name - - Note: standard_attributes section is not mandatory**": {
                        "required": "boolean",
                        "mutable": "boolean"
                    }
                }
            },
            "app_clients": {
                "**arbitrary app client name**": {
                    "logical_name": "string (!)",
                    "name": "string (!) - app client physical name",
                    "access_token_validity": "string - format: duration",
                    "auth_flows": {
                        "user_srp": "boolean (!) - note: user_srp is currently the only valid key"
                    },
                    "auth_session_validity": "string - format: duration",
                    "oauth_settings": {
                        "callback_urls": [
                            "string [_]"
                        ],
                        "default_redirect_uri": "string [_]",
                        "oauth_flows": {
                            "authorization_code_grant": "boolean - Note: oauth_flows section is not mandatory",
                            "client_credentials": "boolean",
                            "implicit_code_grant": "boolean"
                        },
                        "logout_urls": [
                            "string [_] - Note: logout_urls section is not mandatory"
                        ],
                        "oauth_scopes": [
                            "string (!) - See CDK API docs aws_cognito.OAuthScope keys for valid options"
                        ]
                    },
                    "refresh_token_validity": "string - format: duration"
                }
            },
            "domain": {
                "logical_name": "string (!)",
                "custom_domain_name": "string (!)[_]",
                "certificate": "string (!) - must reference a certificate key in certificates.json",
                "dns_recordset": "string (!) - must reference a recordset key in dns.json",
                "depends_on": [
                    "string - ResourceLogicalName - recommend depending on certificate creator custom resource"
                ]
            }
        }
    }
}
```


### dns.json
```json
{
    "**arbitrary recordset name**": {
        "dns_zone_domain": "string (!)[_] - Route53 hosted zone",
        "dns_zone_role_arn": "string (!?)[_] - Role ARN to assume - only mandatory if hosted zone is in a different account - See Additional Requirements section for more information on this role",
        "route53_hosted_zone_id": "string (!)[_]",
        "records": [
            {
                "name": "string (!)[_]",
                "type": "string (!) - valid types are SOA, A, TXT, NS, CNAME, MX, NAPTR, PTR, SRV, SPF, AAAA, CAA, DS, TLSA, SSHFP, SVCB, HTTPS",
                "resource_records": [
                    "string (!)[_] - Note: only resource_records OR alias_target may be present, not both"
                ],
                "alias_target": {
                    "hosted_zone_id": {
                        "source": "string (!) - follows the same placeholder source replacement as in cdk.json, see that section for more details",
                        "**additional keys associated with source**": "string (!)"
                    },
                    "dns_name": {
                        "source": "string (!) - follows the same placeholder source replacement as in cdk.json, see that section for more details,
                        "**additional keys associated with source**": "string (!)"
                    }
                }
            }
        ],
        "custom_resources": {
            "**arbitrary name**": {
                "logical_name": "string (!)",
                "resource_type": "string (!) - must be Custom::CreateDNSRecords",
                "provider": "string (!) - cfn-provider-create-dns-records",
                "depends_on": [
                    "string - ResourceLogicalName - recommend depending on the resources that require the DNS records as this will cause the custom resource to re-run if the resource changes"
                ]
            }
        }
    }
}
```


### dynamodb.json
```json
{
    "resumes": {
        "logical_name": "DynamoDBTableResumes",
        "partition_key": {
            "type": "string (!) - See CDK API docs aws_dynamodb.AttributeType keys for valid options",
            "name": "string (!)"
        },
        "sort_key": {
            "type": "string (!) - See CDK API docs aws_dynamodb.AttributeType keys for valid options - Note: sort_key section is not mandatory",
            "name": "string (!)"
        },
        "global_indexes": {
            "**index physical name - Note: global_indexes section is not mandatory**": {
                "partition_key": {
                    "type": "string (!) - See CDK API docs aws_dynamodb.AttributeType keys for valid options",
                    "name": "string (!)"
                },
                "sort_key": {
                    "type": "string (!) - See CDK API docs aws_dynamodb.AttributeType keys for valid options - Note: sort_key section is not mandatory",
                    "name": "string (!)"
                },
                "projection_type": "string - See CDK API docs aws_dynamodb.ProjectionType keys for valid options",
                "non_key_attributes": [
                    "string - Note: non_key_attributes section is not mandatory"
                ]
            }
        },
        "local_indexes": {
            "**index physical name - Note: local_indexes section is not mandatory**": {
                "partition_key": {
                    "type": "string (!) - See CDK API docs aws_dynamodb.AttributeType keys for valid options",
                    "name": "string (!)"
                },
                "sort_key": {
                    "type": "string (!) - See CDK API docs aws_dynamodb.AttributeType keys for valid options - Note: sort_key section is not mandatory",
                    "name": "string (!)"
                },
                "projection_type": "string - See CDK API docs aws_dynamodb.ProjectionType keys for valid options",
                "non_key_attributes": [
                    "string - Note: non_key_attributes section is not mandatory"
                ]
            }
        }
        "stream": {
            "view_type": "string (!) - See CDK API docs aws_dynamodb.StreamViewType keys for valid options - Note: stream section is not mandatory",
            "function_name": "string (!) - must reference a lambda function key in lambda.json",
            "bisect_batch_on_error": "boolean",
            "batch_size": "int/float",
            "max_batching_window": "string - format: duration",
            "tumbling_window": "string - format: duration",
            "enabled": "boolean",
            "filters": [
                {
                    "pattern": {
                        "dynamodb": {
                            "key": "see DynamoDB event filters for more information on how to write filters"
                        }
                    }
                }
            ],
            "max_record_age", "string - format: duration",
            "metrics_config": "string - See CDK API docs aws_lambda.MetricType keys for valid options",
            "parallelization_factor": "int/float",
            "report_batch_item_failures": "boolean",
            "retry_attempts": "int/float - Default: 0",
            "starting_position": "string - See CDK API docs aws_lambda.StartingPosition keys for valid options"
        }
    }
}
```


### iam.json
```json
{
    "policies": {
        "**physical policy name - Note: All values may include placeholder names**": {
            "logical_name": "string (!)",
            "permissions": {
                "Version": "2012-10-17",
                "Statement": [
                    {
            			"Sid": "string (!)",
            			"Effect": "string (!)",
            			"Action": [
            			    "string (!)"
            			],
            			"Resource": [
            			    "string (!?)[_] - Note: this list can be left blank if a custom resource will be adding resources later"
            			],
            			"**Other IAM statement key-value pairs**": "..."
		            }
                ]
            }
        }
    },
    "roles": {
        "**Role Physical Name**": {
            "logical_name": "string (!)",
            "policies": [
                "string (!) - must reference an iam policy key defined above"
            ],
            "assumed_by": {
				"Service": [
					"string (!)"
				]
			}
        }
    },
    "resource_based_policies": {
        "s3_policies": {
            "**physical policy name - Note: All values may include placeholder names**": {
                "logical_name": "string (!)",
                "permissions": {
                    "Version": "2008-10-17",
                    "Statement": [
                        {
                			"Sid": "string (!)",
                			"Effect": "string (!)",
                			"Action": [
                			    "string (!)"
                			],
                			"Resource": [
                			    "string (!?) - Note: this list can be left blank if a custom resource will be adding resources later"
                			],
                			"**Other IAM statement key-value pairs**": "..."
    		            }
                    ]
                }
            }
        },
        "sns_policies": {
            "**physical policy name - Note: All values may include placeholder names**": {
                "logical_name": "string (!)",
                "permissions": {
                    "Version": "2008-10-17",
                    "Statement": [
                        {
                			"Sid": "string (!)",
                			"Effect": "string (!)",
                			"Action": [
                			    "string (!)"
                			],
                			"Resource": [
                			    "string (!?) - Note: this list can be left blank if a custom resource will be adding resources later"
                			],
                			"**Other IAM statement key-value pairs**": "..."
    		            }
                    ]
                }
            }
        }
    }
}
```


### lambda.json

The implementation of Lambda functions are fairly straightfoward, except for one aspect: custom resources. Currently there is only one custom resource applicable to them, that being the placeholder replacer. In the source code for any lambda function, there can be an arbitrary number of files that can contain placeholders. This is often best used with config files, for when the function needs details about per-deployment information/resources.

The placeholder replacer custom resource is simple in that it only requires to be told what files contain placeholders and what it should depend on. It will also create new versions and update aliases accordingly. However, once a new version has been created, it will be up to different custom resources to update anything that may use the updated Lambda function, such as Lambda@Edge.

```json
{
    "authorize-management-zone-api-gateway": {
        "logical_name": "string (!)",
        "revision_id": "string (!) - Used to explicitly force an update to a lambda function when code has not changed",
        "configuration": {
            "general": {
                "description": "string",
                "memory": "int (!)",
                "ephemeral_storage": "int (!)",
                "timeout": "string (!) - format: duration"
            },
            "permissions": {
                "execution_role": "string (!) - must reference an iam role key defined above"
            }
        },
        "runtime_settings": {
            "runtime": "string (!) - See CDK API docs aws_lambda.Runtime keys for valid options (ALL is not a valid option)",
            "handler": "string (!) - the file path and function to call when executing the function, deliminated by a period"
        },
        "version": {
            "create_version": "boolean - Note: version section is not mandatory",
            "version_options": {
                "max_event_age": "string - format: duration - Note: version options do not currently apply if a custom resource is used",
                "on_failure": "string - must reference a lambda function key within this JSON file",
                "on_success": "string - must reference a lambda function key within this JSON file",
                "retry_attempts": "int/float",
                "code_sha256": "string",
                "description": "string",
                "provisioned_concurrent_executions": "int/float",
                "removal_policy": "string - See CDK API docs aws_cdk.RemovalPolicy keys for valid options"
            }
        },
        "alias": {
            "create_alias": "boolean (!) - Note: alias section is not mandatory",
            "name": "string (!?) - Only required if create_alias is true",
            "description": "string",
            "version": "string - can either be a numeric version or the latest numeric version by using the string latest_version, $LATEST_VERSION is not valid"
        },
        "code_directory": "string (!) - path in repository to the directory containing the source code",
        "allow_cross_stack_references": "boolean - Whether a function can be referenced within other stacks - Note: use with caution, this can easily cause dependency loops",
        "post_deployment_custom_resources": {
            "**arbitrary custom resource-unique name - Note: post_deployment_custom_resources section is not mandatory**": {
                "logical_name": "string (!)",
                "resource_type": "string (!) - currently the only valid option is Custom::LambdaPlaceholderReplacer",
                "provider": "string (!) - must reference a provider key defined in cdk.json",
                "files_with_placeholders": [
                    "string (!) - relative path to file containing placeholders"
                ],
                "depends_on": [
                    "string (!) - ResourceLogicalName - recommend depending on the the custom resource's function"
                ]
            }
        }
    }
}
```

### monitoring.json

This JSON defines what CloudWatch dashboards and alarms are created. cdk-monitoring-constructs is used for facade creation. Within this file, there is a concept called "facade parts". Facade parts are a top-down definition of what is displayed on the CloudWatch dashboard. The only facade parts currently available are "header" and "monitor". A header facade part defines a small, medium, or large header. A monitor facade part defines a series of metrics to display and to send notifications to if a topic is provided (or to not send a notification).

The only alarms currently available are AWS pre-defined alarms.

```json
{
    "facades": {
        "**arbitrary facade name**": {
            "logical_name": "string (!)",
            "region": "string (!)[_]",
            "type": "string (!) - currently the only options available are api_gateway and cloudfront",
            "alarm_defaults": {
                "alarm_name_prefix": "string (!)",
                "action_type": "string (!) - currently the only option available is sns_topic",
                "action": {
                    "on_alarm_topic": "string (!) - must reference an SNS topic key in sns.json",
                    "on_insufficient_data_topic": "string - must reference an SNS topic key in sns.json",
                    "on_ok_topic": "string - must reference an SNS topic key in sns.json",
                }
            },
            "facade_parts": [
                {
                    "type": "string (!) - currently the only options are header and monitor (this is a header example)",
                    "config": {
                        "type": "string (!) - must be either small, medium, or large",
                        "text": "string (!) - header text"
                    }
                },
                {
                    "type": "string (!) - currently the only options are header and monitor (this is a monitor example)",
                    "config": {
                        "monitored_resource_logical_name": "string (!) - must reference a resource logical name",
                        "metric_category": "string (!?) - only required if facade type is api_gateway - must be either by_api_name or by_method",
                        "alarm_friendly_name": "string (!) - regex: [a-zA-Z0-9-]{1,255}",
                        "human_readable_name": "string (!)",
                        "fill_tps_with_zeroes": "boolean",
                        "add_to_alarm_dashboard": "boolean (!)",
                        "predefined_alarms": [
                            {
                                "error_name": "string (!) - see cdk-monitoring-constructs API Python docs MonitoringFacade.monitor_api_gateway or MonitoringFacade.monitor_cloud_front_distribution alarms for valid options",
                                "logical_name_suffix": "string (!) - cdk-monitoring-constructs generates a logical name based on the facade name, type of facade, and monitored resource, this appends onto that logical name to ensure a globally unique logical name",
                                "threshold_details": {
                                    "threshold_type": "string (!) - see the add alarm method for the valid option",
                                    "threshold_arguments": [
                                        {
                                            "name": "string (!) - see the threshold definition for valid argument names",
                                            "value": "variable type (!) - see the threshold definition argument for valid values"
                                        }
                                    ]
                                }
                            }
                        ]
                    }
                }
            ]
        }
    }
}
```


### s3.json
```json
{
    "**bucket physical name**": {
        "logical_name": "string (!)",
        "versioned": "boolean (!)",
        "event_notifications": [
            {
                "destination_type": "string (!) - currently the only valid option is lambda - Note: event_notifications section is not mandatory",
                "destination": {
                    "function_name": "string (!) - must reference a lambda function key in lambda.json",
                    "function_alias": "string - if present, it must match the name of the alias in the lambda function's definition"
                },
                "event_types": [
                    "string (!) - See CDK API docs aws_s3.EventType keys for valid options"
                ],
                "prefix": "string - path to object without leading slash",
                "suffix": "string"
            }
        ],
        "cors": [
            {
                "allowed_headers": [
                    "string - Note: cors section is not mandatory"
                ],
                "allowed_methods": [
                    "string - See CDK API docs aws_s3.HttpMethods keys for valid options"
                ],
                "allowed_origins": [
                    "string [_]"
                ],
                "expose_headers": [
                    "string"
                ],
                "id": "string - unique identifier for the rule",
                "max_age": "string [_] - numerical time in seconds to cache the preflight response in the browser"
            }
        ],
        "bucket_policy": "string - must reference an S3 resource based policy key in iam.json",
        "encryption": "string - See CDK API docs aws_s3.BucketEncryption keys for valid options - Note: customizing or specifying an existing KMS key is currently not supported",
        "block_public_access": {
            "block_all": "boolean (!?) - Note: either block_all or all four other options must be specified, if all options are specific, block_all takes precedence",
            "block_public_acls": "boolean (!?) - see block_all note",
            "block_public_policy": "boolean (!?) - see block_all note",
            "ignore_public_acls": "boolean (!?) - see block_all note",
            "restrict_public_buckets": "boolean (!?) - see block_all note"
        },
        "enforce_ssl": "boolean (!)",
        "retain_in_prod": "boolean (!)",
        "post_deployment_custom_resources": {
            "**arbitrary custom resource-unique name - Note: post_deployment_custom_resources section is not mandatory**": {
                "logical_name": "string (!)",
                "resource_type": "string (!) - Note: currently the only valid option is Custom::EmptyBucket",
                "provider": "string (!) - must reference a provider key defined in cdk.json",
                "empty_on_prod": "boolean (!)",
                "depends_on": [
                    "string - ResourceLogicalName - recommend depending on the custom resource's bucket"
                ]
            }
        }
    }
}
```


### sns.json
```json
{
    "topics": {
        "**arbitrary name**": {
            "logical_name": "string (!)",
            "display_name": "string (!)",
            "subscriptions": {
                "**arbitrary name**": {
                    "logical_name": "string (!)",
                    "protocol": "string (!) - Note: currently the only supported protocol is EMAIL",
                    "endpoint": "string (!)[_]"
                }
            },
            "enforce_ssl": "boolean - Default: true",
            "resource_policy": {
                "logical_name": "string (!) - Note: resource_policy section is not mandatory",
                "policy_name": "string (!) - must reference an SNS resource based policy key in iam.json"
            }
        }
    }
}
```
