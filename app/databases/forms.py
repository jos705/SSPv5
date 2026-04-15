from __future__ import annotations

import re

from flask_wtf import FlaskForm
from wtforms import SelectField, StringField, SubmitField, TextAreaField
from wtforms.validators import DataRequired, Length, Optional, Regexp, ValidationError

from ..models import PgInstance

_DB_NAME_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]{0,62}$")


class CreateDatabaseForm(FlaskForm):
    instance_id = SelectField("Instance", coerce=int, validators=[DataRequired()])
    database_name = StringField(
        "Database name",
        validators=[
            DataRequired(),
            Length(min=1, max=63),
            Regexp(
                r"^[a-zA-Z_][a-zA-Z0-9_]*$",
                message="Use only letters, digits, and underscores. Must start with a letter or underscore.",
            ),
        ],
    )
    reason = TextAreaField(
        "Reason",
        validators=[Optional(), Length(max=500)],
        render_kw={"rows": 2, "placeholder": "Briefly describe why you need this database (optional)"},
    )
    submit = SubmitField("Create database")

    def set_instance_choices(self, instances: list[PgInstance]) -> None:
        self.instance_id.choices = [
            (i.id, f"{i.instance_name}  ({i.hostname}:{i.port})")
            for i in instances
        ]
