#
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.
import six

from heat.common import exception
from heat.common.i18n import _
from heat.engine import attributes
from heat.engine import constraints
from heat.engine import properties
from heat.engine.resources.openstack.neutron import neutron
from heat.engine.resources.openstack.neutron import port
from heat.engine.resources.openstack.neutron import router
from heat.engine import support


class FloatingIP(neutron.NeutronResource):
    PROPERTIES = (
        FLOATING_NETWORK_ID, FLOATING_NETWORK, VALUE_SPECS,
        PORT_ID, FIXED_IP_ADDRESS, FLOATING_IP_ADDRESS,
    ) = (
        'floating_network_id', 'floating_network', 'value_specs',
        'port_id', 'fixed_ip_address', 'floating_ip_address',
    )

    ATTRIBUTES = (
        ROUTER_ID, TENANT_ID, FLOATING_NETWORK_ID_ATTR, FIXED_IP_ADDRESS_ATTR,
        FLOATING_IP_ADDRESS_ATTR, PORT_ID_ATTR,
    ) = (
        'router_id', 'tenant_id', 'floating_network_id', 'fixed_ip_address',
        'floating_ip_address', 'port_id',
    )

    properties_schema = {
        FLOATING_NETWORK_ID: properties.Schema(
            properties.Schema.STRING,
            support_status=support.SupportStatus(
                status=support.HIDDEN,
                version='5.0.0',
                message=_('Use property %s.') % FLOATING_NETWORK,
                previous_status=support.SupportStatus(
                    status=support.DEPRECATED,
                    version='2014.2'
                )
            ),
            constraints=[
                constraints.CustomConstraint('neutron.network')
            ],
        ),
        FLOATING_NETWORK: properties.Schema(
            properties.Schema.STRING,
            _('Network to allocate floating IP from.'),
            support_status=support.SupportStatus(version='2014.2'),
            required=True,
            constraints=[
                constraints.CustomConstraint('neutron.network')
            ],
        ),
        VALUE_SPECS: properties.Schema(
            properties.Schema.MAP,
            _('Extra parameters to include in the "floatingip" object in the '
              'creation request. Parameters are often specific to installed '
              'hardware or extensions.'),
            default={}
        ),
        PORT_ID: properties.Schema(
            properties.Schema.STRING,
            _('ID of an existing port with at least one IP address to '
              'associate with this floating IP.'),
            update_allowed=True,
            constraints=[
                constraints.CustomConstraint('neutron.port')
            ]
        ),
        FIXED_IP_ADDRESS: properties.Schema(
            properties.Schema.STRING,
            _('IP address to use if the port has multiple addresses.'),
            update_allowed=True,
            constraints=[
                constraints.CustomConstraint('ip_addr')
            ]
        ),
        FLOATING_IP_ADDRESS: properties.Schema(
            properties.Schema.STRING,
            _('IP address of the floating IP. NOTE: The default policy '
              'setting in Neutron restricts usage of this property to '
              'administrative users only.'),
            constraints=[
                constraints.CustomConstraint('ip_addr')
            ],
            support_status=support.SupportStatus(version='5.0.0'),
        ),
    }

    attributes_schema = {
        ROUTER_ID: attributes.Schema(
            _('ID of the router used as gateway, set when associated with a '
              'port.'),
            type=attributes.Schema.STRING
        ),
        TENANT_ID: attributes.Schema(
            _('The tenant owning this floating IP.'),
            type=attributes.Schema.STRING
        ),
        FLOATING_NETWORK_ID_ATTR: attributes.Schema(
            _('ID of the network in which this IP is allocated.'),
            type=attributes.Schema.STRING
        ),
        FIXED_IP_ADDRESS_ATTR: attributes.Schema(
            _('IP address of the associated port, if specified.'),
            type=attributes.Schema.STRING
        ),
        FLOATING_IP_ADDRESS_ATTR: attributes.Schema(
            _('The allocated address of this IP.'),
            type=attributes.Schema.STRING
        ),
        PORT_ID_ATTR: attributes.Schema(
            _('ID of the port associated with this IP.'),
            type=attributes.Schema.STRING
        ),
    }

    def translation_rules(self, props):
        return [
            properties.TranslationRule(
                props,
                properties.TranslationRule.REPLACE,
                [self.FLOATING_NETWORK],
                value_path=[self.FLOATING_NETWORK_ID]
            )
        ]

    def add_dependencies(self, deps):
        super(FloatingIP, self).add_dependencies(deps)

        for resource in six.itervalues(self.stack):
            # depend on any RouterGateway in this template with the same
            # network_id as this floating_network_id
            if resource.has_interface('OS::Neutron::RouterGateway'):
                gateway_network = resource.properties.get(
                    router.RouterGateway.NETWORK) or resource.properties.get(
                        router.RouterGateway.NETWORK_ID)
                floating_network = self.properties[self.FLOATING_NETWORK]
                if gateway_network == floating_network:
                    deps += (self, resource)

            # depend on any RouterInterface in this template which interfaces
            # with the same subnet that this floating IP's port is assigned
            # to
            elif resource.has_interface('OS::Neutron::RouterInterface'):

                def port_on_subnet(resource, subnet):
                    if not resource.has_interface('OS::Neutron::Port'):
                        return False
                    fixed_ips = resource.properties.get(port.Port.FIXED_IPS)
                    if not fixed_ips:
                        p_net = (resource.properties.get(port.Port.NETWORK) or
                                 resource.properties.get(port.Port.NETWORK_ID))
                        if p_net and p_net != 'None':
                            subnets = self.client().show_network(p_net)[
                                'network']['subnets']
                            return subnet in subnets
                    else:
                        for fixed_ip in resource.properties.get(
                                port.Port.FIXED_IPS):

                            port_subnet = (
                                fixed_ip.get(port.Port.FIXED_IP_SUBNET)
                                or fixed_ip.get(port.Port.FIXED_IP_SUBNET_ID))
                            return subnet == port_subnet
                    return False

                interface_subnet = (
                    resource.properties.get(router.RouterInterface.SUBNET) or
                    resource.properties.get(router.RouterInterface.SUBNET_ID))
                # during create we have only unresolved value for functions, so
                # cat not use None value for building correct dependencies
                if interface_subnet != 'None':
                    for d in deps.graph()[self]:
                        if port_on_subnet(d, interface_subnet):
                            deps += (self, resource)
                            break
            # depend on Router with EXTERNAL_GATEWAY_NETWORK property
            # this template with the same network_id as this
            # floating_network_id
            elif resource.has_interface('OS::Neutron::Router'):
                gateway = resource.properties.get(
                    router.Router.EXTERNAL_GATEWAY)
                if gateway:
                    gateway_network = gateway.get(
                        router.Router.EXTERNAL_GATEWAY_NETWORK)
                    floating_network = self.properties[self.FLOATING_NETWORK]
                    if gateway_network == floating_network:
                        deps += (self, resource)

    def validate(self):
        super(FloatingIP, self).validate()
        # fixed_ip_address cannot be specified without a port_id
        if self.properties[self.PORT_ID] is None and self.properties[
                self.FIXED_IP_ADDRESS] is not None:
            raise exception.ResourcePropertyDependency(
                prop1=self.FIXED_IP_ADDRESS, prop2=self.PORT_ID)

    def handle_create(self):
        props = self.prepare_properties(
            self.properties,
            self.physical_resource_name())
        self.client_plugin().resolve_network(props, self.FLOATING_NETWORK,
                                             'floating_network_id')
        fip = self.client().create_floatingip({
            'floatingip': props})['floatingip']
        self.resource_id_set(fip['id'])

    def _show_resource(self):
        return self.client().show_floatingip(self.resource_id)['floatingip']

    def handle_delete(self):
        try:
            self.client().delete_floatingip(self.resource_id)
        except Exception as ex:
            self.client_plugin().ignore_not_found(ex)

    def handle_update(self, json_snippet, tmpl_diff, prop_diff):
        if prop_diff:
            port_id = prop_diff.get(self.PORT_ID,
                                    self.properties[self.PORT_ID])

            fixed_ip_address = prop_diff.get(
                self.FIXED_IP_ADDRESS,
                self.properties[self.FIXED_IP_ADDRESS])

            request_body = {
                'floatingip': {
                    'port_id': port_id,
                    'fixed_ip_address': fixed_ip_address}}

            self.client().update_floatingip(self.resource_id, request_body)


class FloatingIPAssociation(neutron.NeutronResource):
    PROPERTIES = (
        FLOATINGIP_ID, PORT_ID, FIXED_IP_ADDRESS,
    ) = (
        'floatingip_id', 'port_id', 'fixed_ip_address',
    )

    properties_schema = {
        FLOATINGIP_ID: properties.Schema(
            properties.Schema.STRING,
            _('ID of the floating IP to associate.'),
            required=True,
            update_allowed=True
        ),
        PORT_ID: properties.Schema(
            properties.Schema.STRING,
            _('ID of an existing port with at least one IP address to '
              'associate with this floating IP.'),
            required=True,
            update_allowed=True,
            constraints=[
                constraints.CustomConstraint('neutron.port')
            ]
        ),
        FIXED_IP_ADDRESS: properties.Schema(
            properties.Schema.STRING,
            _('IP address to use if the port has multiple addresses.'),
            update_allowed=True,
            constraints=[
                constraints.CustomConstraint('ip_addr')
            ]
        ),
    }

    def add_dependencies(self, deps):
        super(FloatingIPAssociation, self).add_dependencies(deps)

        for resource in six.itervalues(self.stack):
            if resource.has_interface('OS::Neutron::RouterInterface'):

                def port_on_subnet(resource, subnet):
                    if not resource.has_interface('OS::Neutron::Port'):
                        return False
                    for fixed_ip in resource.properties.get(
                            port.Port.FIXED_IPS):

                        port_subnet = (
                            fixed_ip.get(port.Port.FIXED_IP_SUBNET)
                            or fixed_ip.get(port.Port.FIXED_IP_SUBNET_ID))
                        return subnet == port_subnet
                    return False

                interface_subnet = (
                    resource.properties.get(router.RouterInterface.SUBNET) or
                    resource.properties.get(router.RouterInterface.SUBNET_ID))
                for d in deps.graph()[self]:
                    if port_on_subnet(d, interface_subnet):
                        deps += (self, resource)
                        break

    def handle_create(self):
        props = self.prepare_properties(self.properties, self.name)

        floatingip_id = props.pop(self.FLOATINGIP_ID)

        self.client().update_floatingip(floatingip_id, {
            'floatingip': props})
        self.resource_id_set(self.id)

    def handle_delete(self):
        if not self.resource_id:
            return

        try:
            self.client().update_floatingip(
                self.properties[self.FLOATINGIP_ID],
                {'floatingip': {'port_id': None}})
        except Exception as ex:
            self.client_plugin().ignore_not_found(ex)

    def handle_update(self, json_snippet, tmpl_diff, prop_diff):
        if prop_diff:
            floatingip_id = self.properties[self.FLOATINGIP_ID]
            port_id = self.properties[self.PORT_ID]
            # if the floatingip_id is changed, disassociate the port which
            # associated with the old floatingip_id
            if self.FLOATINGIP_ID in prop_diff:
                try:
                    self.client().update_floatingip(
                        floatingip_id,
                        {'floatingip': {'port_id': None}})
                except Exception as ex:
                    self.client_plugin().ignore_not_found(ex)

            # associate the floatingip with the new port
            floatingip_id = (prop_diff.get(self.FLOATINGIP_ID) or
                             floatingip_id)
            port_id = prop_diff.get(self.PORT_ID) or port_id

            fixed_ip_address = (prop_diff.get(self.FIXED_IP_ADDRESS) or
                                self.properties[self.FIXED_IP_ADDRESS])

            request_body = {
                'floatingip': {
                    'port_id': port_id,
                    'fixed_ip_address': fixed_ip_address}}

            self.client().update_floatingip(floatingip_id, request_body)
            self.resource_id_set(self.id)


def resource_mapping():
    return {
        'OS::Neutron::FloatingIP': FloatingIP,
        'OS::Neutron::FloatingIPAssociation': FloatingIPAssociation,
    }
