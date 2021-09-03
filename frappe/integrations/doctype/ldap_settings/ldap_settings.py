# -*- coding: utf-8 -*-
# Copyright (c) 2015, Frappe Technologies and contributors
# For license information, please see license.txt

from __future__ import unicode_literals
import frappe
from frappe import _, safe_encode
from frappe.model.document import Document
from frappe.twofactor import (should_run_2fa, authenticate_for_2factor,confirm_otp_token)

class LDAPSettings(Document):
	def validate(self):
		if not self.enabled:
			return

		if not self.flags.ignore_mandatory:

			if self.ldap_search_string.count('(') == self.ldap_search_string.count(')') and \
				self.ldap_search_string.startswith('(') and \
				self.ldap_search_string.endswith(')') and \
				self.ldap_search_string and \
				"{0}" in self.ldap_search_string:

				conn = self.connect_to_ldap(base_dn=self.base_dn, password=self.get_password(raise_exception=False))

				try:
					if conn.result['type'] == 'bindResponse' and self.base_dn:
						import ldap3

						conn.search(
							search_base=self.ldap_search_path_user,
							search_filter="(objectClass=*)",
							attributes=self.get_ldap_attributes())

						conn.search(
							search_base=self.ldap_search_path_group,
							search_filter="(objectClass=*)",
							attributes=['cn'])

				except ldap3.core.exceptions.LDAPAttributeError as ex:
					frappe.throw(_("LDAP settings incorrect. validation response was: {0}").format(ex),
						title=_("Misconfigured"))

				except ldap3.core.exceptions.LDAPNoSuchObjectResult:
					frappe.throw(_("Ensure the user and group search paths are correct."),
						title=_("Misconfigured"))

				if self.ldap_directory_server.lower() == 'custom':
					if not self.ldap_group_member_attribute or not self.ldap_group_mappings_section:
						frappe.throw(_("Custom LDAP Directoy Selected, please ensure 'LDAP Group Member attribute' and 'LDAP Group Mappings' are entered"),
						title=_("Misconfigured"))

			else:
				frappe.throw(_("LDAP Search String must be enclosed in '()' and needs to contian the user placeholder {0}, eg sAMAccountName={0}"))

	def connect_to_ldap(self, base_dn, password, read_only=True):
		try:
			import ldap3
			import ssl

			if self.require_trusted_certificate == 'Yes':
				tls_configuration = ldap3.Tls(validate=ssl.CERT_REQUIRED, version=ssl.PROTOCOL_TLSv1)
			else:
				tls_configuration = ldap3.Tls(validate=ssl.CERT_NONE, version=ssl.PROTOCOL_TLSv1)

			if self.local_private_key_file:
				tls_configuration.private_key_file = self.local_private_key_file
			if self.local_server_certificate_file:
				tls_configuration.certificate_file = self.local_server_certificate_file
			if self.local_ca_certs_file:
				tls_configuration.ca_certs_file = self.local_ca_certs_file

			server = ldap3.Server(host=self.ldap_server_url, tls=tls_configuration)
			bind_type = ldap3.AUTO_BIND_TLS_BEFORE_BIND if self.ssl_tls_mode == "StartTLS" else True

			conn = ldap3.Connection(
				server=server,
				user=base_dn,
				password=password,
				auto_bind=bind_type,
				read_only=read_only,
				raise_exceptions=True)

			return conn

		except ImportError:
			msg = _("Please Install the ldap3 library via pip to use ldap functionality.")
			frappe.throw(msg, title=_("LDAP Not Installed"))
		except ldap3.core.exceptions.LDAPInvalidCredentialsResult:
			frappe.throw(_("Invalid username or password"))
		except Exception as ex:
			frappe.throw(_(str(ex)))

	@staticmethod
	def get_ldap_client_settings():
		# return the settings to be used on the client side.
		result = {
			"enabled": False
		}
		ldap = frappe.get_doc("LDAP Settings")
		if ldap.enabled:
			result["enabled"] = True
			result["method"] = "frappe.integrations.doctype.ldap_settings.ldap_settings.login"
		return result

	@classmethod
	def update_user_fields(cls, user, user_data):

		updatable_data = {key: value for key, value in user_data.items() if key != 'email'}

		for key, value in updatable_data.items():
			setattr(user, key, value)
		user.save(ignore_permissions=True)

	def sync_roles(self, user, additional_groups=None):

		current_roles = set([d.role for d in user.get("roles")])

		needed_roles = set()
		needed_roles.add(self.default_role)

		lower_groups = [g.lower() for g in additional_groups or []]

		all_mapped_roles = {r.erpnext_role for r in self.ldap_groups}
		matched_roles = {r.erpnext_role for r in self.ldap_groups if r.ldap_group.lower() in lower_groups}
		unmatched_roles = all_mapped_roles.difference(matched_roles)
		needed_roles.update(matched_roles)
		roles_to_remove = current_roles.intersection(unmatched_roles)

		if not needed_roles.issubset(current_roles):
			missing_roles = needed_roles.difference(current_roles)
			user.add_roles(*missing_roles)

		user.remove_roles(*roles_to_remove)

	def create_or_update_user(self, user_data, groups=None):
		user = None
		if frappe.db.exists("User", user_data['email']):
			user = frappe.get_doc("User", user_data['email'])
			LDAPSettings.update_user_fields(user=user, user_data=user_data)
		else:
			doc = user_data
			doc.update({
				"doctype": "User",
				"send_welcome_email": 0,
				"language": "",
				"user_type": "System User",
				# "roles": [{
				# 	"role": self.default_role
				# }]
			})
			user = frappe.get_doc(doc)
			user.insert(ignore_permissions=True)
		# always add default role.
		user.add_roles(self.default_role)
		self.sync_roles(user, groups)

		return user

	def get_ldap_attributes(self):
		ldap_attributes = [self.ldap_email_field, self.ldap_username_field, self.ldap_first_name_field]

		if self.ldap_group_field:
			ldap_attributes.append(self.ldap_group_field)

		if self.ldap_middle_name_field:
			ldap_attributes.append(self.ldap_middle_name_field)

		if self.ldap_last_name_field:
			ldap_attributes.append(self.ldap_last_name_field)

		if self.ldap_phone_field:
			ldap_attributes.append(self.ldap_phone_field)

		if self.ldap_mobile_field:
			ldap_attributes.append(self.ldap_mobile_field)

		return ldap_attributes


	def fetch_ldap_groups(self, user, conn):
		import ldap3

		if type(user) is not ldap3.abstract.entry.Entry:
			raise TypeError("Invalid type, attribute {0} must be of type '{1}'".format('user', 'ldap3.abstract.entry.Entry'))

		if type(conn) is not ldap3.core.connection.Connection:
			raise TypeError("Invalid type, attribute {0} must be of type '{1}'".format('conn', 'ldap3.Connection'))

		fetch_ldap_groups = None

		ldap_object_class = None
		ldap_group_members_attribute = None


		if self.ldap_directory_server.lower() == 'active directory':

			ldap_object_class = 'Group'
			ldap_group_members_attribute = 'member'
			user_search_str = user.entry_dn


		elif self.ldap_directory_server.lower() == 'openldap':

			ldap_object_class = 'posixgroup'
			ldap_group_members_attribute = 'memberuid'
			user_search_str = getattr(user, self.ldap_username_field).value

		elif self.ldap_directory_server.lower() == 'custom':

			ldap_object_class = self.ldap_group_objectclass
			ldap_group_members_attribute = self.ldap_group_member_attribute
			user_search_str = getattr(user, self.ldap_username_field).value

		else:
			# NOTE: depreciate this else path
			# this path will be hit for everyone with preconfigured ldap settings. this must be taken into account so as not to break ldap for those users.

			if self.ldap_group_field:

				fetch_ldap_groups = getattr(user, self.ldap_group_field).values

		if ldap_object_class is not None:
			conn.search(
				search_base=self.ldap_search_path_group,
				search_filter="(&(objectClass={0})({1}={2}))".format(ldap_object_class,ldap_group_members_attribute, user_search_str),
				attributes=['cn']) # Build search query

		if len(conn.entries) >= 1:

			fetch_ldap_groups = []
			for group in conn.entries:
				fetch_ldap_groups.append(group['cn'].value)

		return fetch_ldap_groups




	def authenticate(self, username, password):

		if not self.enabled:
			frappe.throw(_("LDAP is not enabled."))

		user_filter = self.ldap_search_string.format(username)
		ldap_attributes = self.get_ldap_attributes()

		conn = self.connect_to_ldap(self.base_dn, self.get_password(raise_exception=False))

		try:
			import ldap3

			conn.search(
				search_base=self.ldap_search_path_user,
				search_filter="{0}".format(user_filter),
				attributes=ldap_attributes)

			if len(conn.entries) == 1 and conn.entries[0]:
				user = conn.entries[0]

				groups = self.fetch_ldap_groups(user, conn)

				# only try and connect as the user, once we have their fqdn entry.
				if user.entry_dn and password and conn.rebind(user=user.entry_dn, password=password):

					return self.create_or_update_user(self.convert_ldap_entry_to_dict(user), groups=groups)

			raise ldap3.core.exceptions.LDAPInvalidCredentialsResult # even though nothing foundor failed authentication raise invalid credentials

		except ldap3.core.exceptions.LDAPInvalidFilterError:
			frappe.throw(_("Please use a valid LDAP search filter"), title=_("Misconfigured"))

		except ldap3.core.exceptions.LDAPInvalidCredentialsResult:
			frappe.throw(_("Invalid username or password"))


	def reset_password(self, user, password, logout_sessions=False):
		from ldap3 import HASHED_SALTED_SHA, MODIFY_REPLACE
		from ldap3.utils.hashed import hashed

		search_filter = "({0}={1})".format(self.ldap_email_field, user)

		conn = self.connect_to_ldap(self.base_dn, self.get_password(raise_exception=False),
			read_only=False)

		if conn.search(
			search_base=self.ldap_search_path_user,
			search_filter=search_filter,
			attributes=self.get_ldap_attributes()
		):
			if conn.entries and conn.entries[0]:
				entry_dn = conn.entries[0].entry_dn
				hashed_password = hashed(HASHED_SALTED_SHA, safe_encode(password))
				changes = {'userPassword': [(MODIFY_REPLACE, [hashed_password])]}
				if conn.modify(entry_dn, changes=changes):
					if logout_sessions:
						from frappe.sessions import clear_sessions
						clear_sessions(user=user, force=True)
					frappe.msgprint(_("Password changed successfully."))
				else:
					frappe.throw(_("Failed to change password."))
			else:
				frappe.throw(_("No Entry for the User {0} found within LDAP!").format(user))
		else:
			frappe.throw(_("No LDAP User found for email: {0}").format(user))

	def convert_ldap_entry_to_dict(self, user_entry):

		# support multiple email values
		email = user_entry[self.ldap_email_field]

		data = {
			'username': user_entry[self.ldap_username_field].value,
			'email': str(email.value[0] if isinstance(email.value, list) else email.value),
			'first_name': user_entry[self.ldap_first_name_field].value
		}

		# optional fields

		if self.ldap_middle_name_field:
			data['middle_name'] = user_entry[self.ldap_middle_name_field].value

		if self.ldap_last_name_field:
			data['last_name'] = user_entry[self.ldap_last_name_field].value

		if self.ldap_phone_field:
			data['phone'] = user_entry[self.ldap_phone_field].value

		if self.ldap_mobile_field:
			data['mobile_no'] = user_entry[self.ldap_mobile_field].value

		return data


@frappe.whitelist(allow_guest=True)
def login():
	# LDAP LOGIN LOGIC
	args = frappe.form_dict
	ldap = frappe.get_doc("LDAP Settings")

	user = ldap.authenticate(frappe.as_unicode(args.usr), frappe.as_unicode(args.pwd))

	frappe.local.login_manager.user = user.name
	if should_run_2fa(user.name):
		authenticate_for_2factor(user.name)
		if not confirm_otp_token(frappe.local.login_manager):
			return False
	frappe.local.login_manager.post_login()

	# because of a GET request!
	frappe.db.commit()


@frappe.whitelist()
def reset_password(user, password, logout):
	ldap = frappe.get_doc("LDAP Settings")
	if not ldap.enabled:
		frappe.throw(_("LDAP is not enabled."))
	ldap.reset_password(user, password, logout_sessions=int(logout))
