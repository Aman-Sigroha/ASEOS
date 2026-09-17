from runtime.schemas.plan import Plan, PlanStep


def test_plan_creation():
    plan = Plan(
        task_id="task-001",
        goal="Fix the division bug",
        steps=[
            PlanStep(
                id="step-1",
                description="Find divide implementation",
            ),
            PlanStep(
                id="step-2",
                description="Run tests",
            ),
        ],
    )

    assert plan.task_id == "task-001"
    assert len(plan.steps) == 2
