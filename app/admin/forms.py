from __future__ import annotations

from flask_wtf import FlaskForm
from wtforms import PasswordField, SelectField, StringField, SubmitField, TextAreaField
from wtforms.validators import DataRequired, Email, InputRequired, Length, Optional, ValidationError

from ..models import Team, UserRole

ROLE_CHOICES = [
    (UserRole.ADMIN.value, "Admin"),
    (UserRole.USER.value, "User"),
]


class TeamForm(FlaskForm):
    name = StringField("Team name", validators=[DataRequired(), Length(min=2, max=120)])
    description = TextAreaField("Description", validators=[Optional(), Length(max=255)])
    submit = SubmitField("Save team")


class UserBaseForm(FlaskForm):
    email = StringField("Email", validators=[DataRequired(), Email(), Length(max=255)])
    username = StringField("Username", validators=[DataRequired(), Length(min=2, max=100)])
    role = SelectField("Role", choices=ROLE_CHOICES, validators=[DataRequired()])
    team_id = SelectField("Team", coerce=int, validators=[InputRequired()])

    def set_team_choices(self, teams: list[Team]) -> None:
        self.team_id.choices = [(0, "No team")] + [(team.id, team.name) for team in teams]

    def validate_team_id(self, field) -> None:
        if self.role.data == UserRole.USER.value and field.data == 0:
            raise ValidationError("Regular users must belong to a team.")


class UserCreateForm(UserBaseForm):
    password = PasswordField("Password", validators=[DataRequired(), Length(min=8, max=128)])
    submit = SubmitField("Create user")


class UserEditForm(UserBaseForm):
    password = PasswordField(
        "New password (optional)",
        validators=[Optional(), Length(min=8, max=128)],
    )
    submit = SubmitField("Save changes")

