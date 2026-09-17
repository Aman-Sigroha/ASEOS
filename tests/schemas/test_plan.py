from runtime.schemas.plan import Plan, PlanStep


def test_plan_creation():
    plan = Plan(
        task_id="task-001",
        goal="Fix the division bug",
        steps=[
            PlanStep(
                id="step-1",
                description="Find the divide implementation",
                action_type="SEARCH",
                parameters={
                    "query": "divide",
                    "path": "src/",
                },
            ),
            PlanStep(
                id="step-2",
                description="Read calculator implementation",
                action_type="READ",
                parameters={
                    "path": "src/calculator.py",
                },
            ),
        ],
    )

    assert plan.task_id == "task-001"
    assert plan.goal == "Fix the division bug"
    assert len(plan.steps) == 2

    assert plan.steps[0].action_type == "SEARCH"
    assert plan.steps[0].parameters["query"] == "divide"

    assert plan.steps[1].action_type == "READ"
    assert plan.steps[1].parameters["path"] == "src/calculator.py"
