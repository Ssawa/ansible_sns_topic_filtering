#!/usr/bin/python
# -*- coding: utf-8 -*-
#
# This is a free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This Ansible library is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this library.  If not, see <http://www.gnu.org/licenses/>.


DOCUMENTATION = """
module: sns_topic_filtering
short_description: Manages AWS SNS topics and subscriptions with the addition of FilterPolicies
description:
    - The M(sns_topic) module allows you to create, delete, and manage subscriptions for AWS SNS topics.
version_added: 2.0
author:
  - "Joel Thompson (@joelthompson)"
  - "Fernando Jose Pando (@nand0p)"
  - "CJ DiMaggio" (@ssawa)
options:
  name:
    description:
      - The name or ARN of the SNS topic to converge
    required: True
  state:
    description:
      - Whether to create or destroy an SNS topic
    required: False
    default: present
    choices: ["absent", "present"]
  display_name:
    description:
      - Display name of the topic
    required: False
    default: None
  policy:
    description:
      - Policy to apply to the SNS topic
    required: False
    default: None
  delivery_policy:
    description:
      - Delivery policy to apply to the SNS topic
    required: False
    default: None
  subscriptions:
    description:
      - List of subscriptions to apply to the topic. Note that AWS requires
        subscriptions to be confirmed, so you will need to confirm any new
        subscriptions.
    required: False
    default: []
  purge_subscriptions:
    description:
      - "Whether to purge any subscriptions not listed here. NOTE: AWS does not
        allow you to purge any PendingConfirmation subscriptions, so if any
        exist and would be purged, they are silently skipped. This means that
        somebody could come back later and confirm the subscription. Sorry.
        Blame Amazon."
    required: False
    default: True
extends_documentation_fragment: aws
requirements: [ "boto" ]
"""

EXAMPLES = """

- name: Create alarm SNS topic
  sns_topic:
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

"""

RETURN = '''
sns_arn:
    description: The ARN of the topic you are modifying
    type: string
    sample: "arn:aws:sns:us-east-1:123456789012:my_topic_name"

sns_topic:
    description: Dict of sns topic details
    type: dict
    sample:
      name: sns-topic-name
      state: present
      display_name: default
      policy: {}
      delivery_policy: {}
      subscriptions_new: []
      subscriptions_existing: []
      subscriptions_deleted: []
      subscriptions_added: []
      subscriptions_purge': false
      check_mode: false
      topic_created: false
      topic_deleted: false
      attributes_set: []
'''

import time
import json
import re

try:
    from botocore.exceptions import ClientError
    HAS_BOTO3 = True
except ImportError:
    HAS_BOTO3 = False

from ansible.module_utils.basic import AnsibleModule
from ansible.module_utils.ec2 import boto3_conn, ec2_argument_spec, get_aws_connection_info, HAS_BOTO3

class SnsTopicManager(object):
    """ Handles SNS Topic creation and destruction """

    def __init__(self,
                 module,
                 name,
                 state,
                 display_name,
                 policy,
                 delivery_policy,
                 subscriptions,
                 purge_subscriptions,
                 check_mode,
                 region,
                 ec2_url,
                 **aws_connect_params):

        self.module = module
        self.region = region
        self.ec2_url = ec2_url
        self.aws_connect_params = aws_connect_params
        self.connection = self._get_boto_connection()
        self.changed = False
        self.name = name
        self.state = state
        self.display_name = display_name
        self.policy = policy
        self.delivery_policy = delivery_policy
        self.subscriptions = subscriptions
        self.subscriptions_existing = []
        self.subscriptions_deleted = []
        self.subscriptions_added = []
        self.purge_subscriptions = purge_subscriptions
        self.check_mode = check_mode
        self.topic_created = False
        self.topic_deleted = False
        self.arn_topic = None
        self.attributes_set = []

    def _get_boto_connection(self):
        try:
            return boto3_conn(self.module, conn_type='client', resource='sns', region=self.region, endpoint=self.ec2_url,
                **self.aws_connect_params)
        except ClientError as err:
            self.module.fail_json(msg=err.message)

    def _get_all_topics(self):
        next_token = ''
        topics = []
        while True:
            try:
                response = self.connection.list_topics(NextToken=next_token)
            except ClientError as err:
                self.module.fail_json(msg=err.message)
            topics.extend(response['Topics'])
            next_token = response.get('NextToken', '')
            if not next_token:
                break

        return [t['TopicArn'] for t in topics]


    def _arn_topic_lookup(self):
        # topic names cannot have colons, so this captures the full topic name
        all_topics = self._get_all_topics()
        lookup_topic = ':%s' % self.name
        for topic in all_topics:
            if topic.endswith(lookup_topic):
                return topic


    def _create_topic(self):
        self.changed = True
        self.topic_created = True
        if not self.check_mode:
            self.connection.create_topic(Name=self.name)
            self.arn_topic = self._arn_topic_lookup()
            while not self.arn_topic:
                time.sleep(3)
                self.arn_topic = self._arn_topic_lookup()


    def _set_topic_attrs(self):
        topic_attributes = self.connection.get_topic_attributes(TopicArn=self.arn_topic)['Attributes']

        if self.display_name and self.display_name != topic_attributes['DisplayName']:
            self.changed = True
            self.attributes_set.append('display_name')
            if not self.check_mode:
                self.connection.set_topic_attributes(TopicArn=self.arn_topic, AttributeName='DisplayName',
                    AttributeValue=self.display_name)

        if self.policy and self.policy != json.loads(topic_attributes['Policy']):
            self.changed = True
            self.attributes_set.append('policy')
            if not self.check_mode:
                self.connection.set_topic_attributes(TopicArn=self.arn_topic, AttributeName='Policy',
                    AttributeValue=json.dumps(self.policy))

        if self.delivery_policy and ('DeliveryPolicy' not in topic_attributes or \
           self.delivery_policy != json.loads(topic_attributes['DeliveryPolicy'])):
            self.changed = True
            self.attributes_set.append('delivery_policy')
            if not self.check_mode:
                self.connection.set_topic_attributes(TopicArn=self.arn_topic, AttributeName='DeliveryPolicy',
                    AttributeValue=json.dumps(self.delivery_policy))


    def _canonicalize_endpoint(self, protocol, endpoint):
        if protocol == 'sms':
            return re.sub('[^0-9]*', '', endpoint)
        return endpoint


    def _get_topic_subs(self):
        next_token = ''
        while True:
            response = self.connection.list_subscriptions_by_topic(TopicArn=self.arn_topic, NextToken=next_token)
            self.subscriptions_existing.extend(response['Subscriptions'])
            next_token = response.get('NextToken', '')
            if not next_token:
                break

    def _set_topic_subs(self):
        subscriptions_existing_map = {}

        desired_subscriptions = dict()
        for sub in self.subscriptions:
            key = (sub['protocol'], self._canonicalize_endpoint(sub['protocol'], sub['endpoint']))
            desired_subscriptions[key] = sub

        if self.subscriptions_existing:
            for sub in self.subscriptions_existing:
                sub_key = (sub['Protocol'], sub['Endpoint'])
                subscriptions_existing_map[sub_key] = sub
                if self.purge_subscriptions and sub_key not in desired_subscriptions and \
                    sub['SubscriptionArn'] != 'PendingConfirmation':
                    self.changed = True
                    self.subscriptions_deleted.append(sub_key)
                    if not self.check_mode:
                        self.connection.unsubscribe(sub['SubscriptionArn'])

        for key, sub in desired_subscriptions.items():
            filter_policy = sub.get('filterPolicy', None)
            if filter_policy:
                filter_policy = json.dumps(filter_policy)

            if key not in subscriptions_existing_map:
                self.changed = True
                self.subscriptions_added.append(sub)
                if not self.check_mode:
                    sub_arn = self.connection.subscribe(TopicArn=self.arn_topic,
                        Protocol=sub['protocol'], Endpoint=sub['endpoint'])['SubscriptionArn']
                    if (filter_policy):
                        self.connection.set_subscription_attributes(SubscriptionArn=sub_arn,
                            AttributeName='FilterPolicy', AttributeValue=filter_policy)

            else:
                existing = subscriptions_existing_map[key]
                attributes = self.connection.get_subscription_attributes(SubscriptionArn=existing['SubscriptionArn'])['Attributes']
                existing_filter_policy = attributes.get('FilterPolicy', None)
                if (filter_policy != existing_filter_policy):
                    if (filter_policy):
                        self.changed = True
                        if not self.check_mode:
                            self.connection.set_subscription_attributes(SubscriptionArn=existing['SubscriptionArn'],
                                AttributeName='FilterPolicy', AttributeValue=filter_policy)

    def _delete_subscriptions(self):
        # NOTE: subscriptions in 'PendingConfirmation' timeout in 3 days
        #       https://forums.aws.amazon.com/thread.jspa?threadID=85993
        for sub in self.subscriptions_existing:
            if sub['SubscriptionArn'] != 'PendingConfirmation':
                self.subscriptions_deleted.append(sub['SubscriptionArn'])
                self.changed = True
                if not self.check_mode:
                    self.connection.unsubscribe(SubscriptionArn=sub['SubscriptionArn'])


    def _delete_topic(self):
        self.topic_deleted = True
        self.changed = True
        if not self.check_mode:
            self.connection.delete_topic(TopicArn=self.arn_topic)


    def ensure_ok(self):
        self.arn_topic = self._arn_topic_lookup()
        if not self.arn_topic:
            self._create_topic()
        self._set_topic_attrs()
        self._get_topic_subs()
        self._set_topic_subs()

    def ensure_gone(self):
        self.arn_topic = self._arn_topic_lookup()
        if self.arn_topic:
           self._get_topic_subs()
           if self.subscriptions_existing:
               self._delete_subscriptions()
           self._delete_topic()


    def get_info(self):
        info = {
            'name': self.name,
            'state': self.state,
            'display_name': self.display_name,
            'policy': self.policy,
            'delivery_policy': self.delivery_policy,
            'subscriptions_new': self.subscriptions,
            'subscriptions_existing': self.subscriptions_existing,
            'subscriptions_deleted': self.subscriptions_deleted,
            'subscriptions_added': self.subscriptions_added,
            'subscriptions_purge': self.purge_subscriptions,
            'check_mode': self.check_mode,
            'topic_created': self.topic_created,
            'topic_deleted': self.topic_deleted,
            'attributes_set': self.attributes_set
        }

        return info



def main():
    argument_spec = ec2_argument_spec()
    argument_spec.update(
        dict(
            name=dict(type='str', required=True),
            state=dict(type='str', default='present', choices=['present',
                'absent']),
            display_name=dict(type='str', required=False),
            policy=dict(type='dict', required=False),
            delivery_policy=dict(type='dict', required=False),
            subscriptions=dict(default=[], type='list', required=False),
            purge_subscriptions=dict(type='bool', default=True),
        )
    )

    module = AnsibleModule(argument_spec=argument_spec,
                           supports_check_mode=True)

    if not HAS_BOTO3:
        module.fail_json(msg='boto3 required for this module')

    name = module.params.get('name')
    state = module.params.get('state')
    display_name = module.params.get('display_name')
    policy = module.params.get('policy')
    delivery_policy = module.params.get('delivery_policy')
    subscriptions = module.params.get('subscriptions')
    purge_subscriptions = module.params.get('purge_subscriptions')
    check_mode = module.check_mode

    region, ec2_url, aws_connect_params = get_aws_connection_info(module, boto3=True)
    if not region:
        module.fail_json(msg="region must be specified")

    sns_topic = SnsTopicManager(module,
                                name,
                                state,
                                display_name,
                                policy,
                                delivery_policy,
                                subscriptions,
                                purge_subscriptions,
                                check_mode,
                                region,
                                ec2_url,
                                **aws_connect_params)

    if state == 'present':
        sns_topic.ensure_ok()

    elif state == 'absent':
        sns_topic.ensure_gone()

    sns_facts = dict(changed=sns_topic.changed,
                     sns_arn=sns_topic.arn_topic,
                     sns_topic=sns_topic.get_info())

    module.exit_json(**sns_facts)


if __name__ == '__main__':
    main()
