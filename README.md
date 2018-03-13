# Ansible SNS Topic Filtering
An addition to ansible's sns_topic module to allow setting a FilterPolicy

Note that currently because of limitations with AWS it is not possible to entirely _delete_ a FilterPolicy (only replace it). In order to simulate this kind of functionality a subscription must be deleted and recreated, which this module currently does **not** do to avoid potentially deleting configured data.

## Example:
```yaml
- name: Create alarm SNS topic
  sns_topic_filtering:
    name: "alarms"
    state: present
    display_name: "alarm SNS topic"
    delivery_policy: 
      http:
        defaultHealthyRetryPolicy: 
            minDelayTarget: 2
            maxDelayTarget: 4
            numRetries: 3
            numMaxDelayRetries: 5
            backoffFunction: "<linear|arithmetic|geometric|exponential>"
        disableSubscriptionOverrides: True
        defaultThrottlePolicy: 
            maxReceivesPerSecond: 10
    subscriptions:
      - endpoint: "my_email_address@example.com"
        protocol: "email"
      - endpoint: "my_mobile_number"
        protocol: "sms"
        filterPolicy:
            store: [ example_corp ]
            event: [ order_placed ]

```
