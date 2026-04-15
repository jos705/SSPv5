from __future__ import annotations

from flask_wtf import FlaskForm
from wtforms import PasswordField, SelectField, StringField, SubmitField, TextAreaField
from wtforms.validators import DataRequired, Email, InputRequired, Length, Optional, ValidationError

from ..models import PermissionLevel, Team, UserRole

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


PERMISSION_CHOICES = [
    (PermissionLevel.DIRECT.value, "Direct (no approval required)"),
    (PermissionLevel.REQUEST.value, "Request (admin approval required)"),
]


class ClusterForm(FlaskForm):
    name = StringField("Cluster name", validators=[DataRequired(), Length(min=2, max=120)])
    load_balancer = StringField(
        "Load balancer hostname / IP",
        validators=[DataRequired(), Length(max=255)],
    )
    description = TextAreaField("Description", validators=[Optional(), Length(max=255)])
    ssh_user = StringField(
        "SSH user",
        validators=[DataRequired(), Length(max=100)],
        default="postgres",
    )
    ssh_key_path = StringField(
        "SSH private key path",
        validators=[Optional(), Length(max=500)],
        description="Leave blank to use the default key (~/.ssh/id_ed25519)",
    )
    # Clusters have 2-3 nodes; at least one is required
    node1 = StringField("Node 1 hostname", validators=[DataRequired(), Length(max=255)])
    node2 = StringField("Node 2 hostname", validators=[Optional(), Length(max=255)])
    node3 = StringField("Node 3 hostname", validators=[Optional(), Length(max=255)])
    submit = SubmitField("Save cluster")

    def node_hostnames(self) -> list[str]:
        """Return the non-empty, stripped node hostnames in order."""
        return [
            h.strip()
            for h in [self.node1.data, self.node2.data, self.node3.data]
            if h and h.strip()
        ]


class TeamClusterPermissionForm(FlaskForm):
    team_id = SelectField("Team", coerce=int, validators=[DataRequired()])
    permission_level = SelectField(
        "Permission level",
        choices=PERMISSION_CHOICES,
        validators=[DataRequired()],
    )
    submit = SubmitField("Grant access")

    def set_team_choices(self, teams: list[Team]) -> None:
        self.team_id.choices = [(t.id, t.name) for t in teams]

