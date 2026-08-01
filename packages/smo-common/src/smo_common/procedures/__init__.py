"""SMO Common — canonical procedure schema and dialect adapters.

See ``docs/PROCEDURE_SCHEMA.md`` for the normative description.
"""
from smo_common.procedures.schema import (
    CANONICAL_SCHEMA_VERSION,
    STEP_TYPES,
    CanonicalProcedure,
    ProcedureStep,
    StepCondition,
    describe_step,
    from_activity_sequence,
    from_builder_steps,
    from_markdown,
    to_canonical,
    validate_steps,
)

__all__ = [
    "CANONICAL_SCHEMA_VERSION",
    "STEP_TYPES",
    "CanonicalProcedure",
    "ProcedureStep",
    "StepCondition",
    "describe_step",
    "from_activity_sequence",
    "from_builder_steps",
    "from_markdown",
    "to_canonical",
    "validate_steps",
]
