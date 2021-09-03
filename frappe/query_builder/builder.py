from pypika import MySQLQuery, Order, PostgreSQLQuery, terms
from pypika.queries import Schema, Table
from frappe.utils import get_table_name


class Base:
	terms = terms
	desc = Order.desc
	Schema = Schema
	Table = Table

	@staticmethod
	def DocType(table_name: str, *args, **kwargs) -> Table:
		table_name = get_table_name(table_name)
		return Table(table_name, *args, **kwargs)


class MariaDB(Base, MySQLQuery):
	Field = terms.Field

	@classmethod
	def from_(cls, table, *args, **kwargs):
		if isinstance(table, str):
			table = cls.DocType(table)
		return super().from_(table, *args, **kwargs)


class Postgres(Base, PostgreSQLQuery):
	field_translation = {"table_name": "relname", "table_rows": "n_tup_ins"}
	schema_translation = {"tables": "pg_stat_all_tables"}
	# TODO: Find a better way to do this
	# These are interdependent query changes that need fixing. These
	# translations happen in the same query. But there is no check to see if
	# the Fields are changed only when a particular `information_schema` schema
	# is used. Replacing them is not straightforward because the "from_"
	# function can not see the arguments passed to the "select" function as
	# they are two different objects. The quick fix used here is to replace the
	# Field names in the "Field" function.

	@classmethod
	def Field(cls, field_name, *args, **kwargs):
		if field_name in cls.field_translation:
			field_name = cls.field_translation[field_name]
		return terms.Field(field_name, *args, **kwargs)

	@classmethod
	def from_(cls, table, *args, **kwargs):
		if isinstance(table, Table):
			if table._schema:
				if table._schema._name == "information_schema":
					table = cls.schema_translation[table._table_name]

		elif isinstance(table, str):
			table = cls.DocType(table)

		return super().from_(table, *args, **kwargs)
