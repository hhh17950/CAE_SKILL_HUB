from app.schemas.common import Name, Parameters


class SolverSettingsRequest(Parameters):
    solver_type: Name
    linear_solver_type: Name
