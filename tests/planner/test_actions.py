from runtime.planner.actions import ActionGenerator
from runtime.schemas.plan import Plan, PlanStep


def test_action_generator_creates_search_action():
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
            )
        ],
    )

    generator = ActionGenerator()

    actions = generator.generate(plan)

    assert len(actions) == 1

    assert actions[0].id == "step-1"
    assert actions[0].type == "SEARCH"
    assert actions[0].payload == {
        "query": "divide",
        "path": "src/",
    }


def test_action_generator_creates_read_action():
    plan = Plan(
        task_id="task-001",
        goal="Fix the division bug",
        steps=[
            PlanStep(
                id="step-2",
                description="Read the calculator implementation",
                action_type="READ",
                parameters={
                    "path": "src/calculator.py",
                },
            )
        ],
    )

    generator = ActionGenerator()

    actions = generator.generate(plan)

    assert len(actions) == 1

    assert actions[0].id == "step-2"
    assert actions[0].type == "READ"
    assert actions[0].payload == {
        "path": "src/calculator.py",
    }


def test_action_generator_preserves_plan_order():
    plan = Plan(
        task_id="task-001",
        goal="Fix the division bug",
        steps=[
            PlanStep(
                id="step-1",
                description="Find divide implementation",
                action_type="SEARCH",
                parameters={
                    "query": "divide",
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
            PlanStep(
                id="step-3",
                description="Run calculator tests",
                action_type="RUN_TEST",
                parameters={
                    "path": "tests/test_calculator.py",
                },
            ),
        ],
    )

    generator = ActionGenerator()

    actions = generator.generate(plan)

    assert len(actions) == 3

    assert [action.id for action in actions] == [
        "step-1",
        "step-2",
        "step-3",
    ]

    assert [action.type for action in actions] == [
        "SEARCH",
        "READ",
        "RUN_TEST",
    ]

    assert actions[2].payload == {
        "path": "tests/test_calculator.py",
    }


def test_action_generator_preserves_empty_parameters():
    plan = Plan(
        task_id="task-001",
        goal="Inspect Git diff",
        steps=[
            PlanStep(
                id="step-1",
                description="Inspect current Git diff",
                action_type="GIT_DIFF",
            )
        ],
    )

    generator = ActionGenerator()

    actions = generator.generate(plan)

    assert len(actions) == 1
    assert actions[0].type == "GIT_DIFF"
    assert actions[0].payload == {}


def test_action_generator_handles_empty_plan():
    plan = Plan(
        task_id="task-001",
        goal="Do nothing",
        steps=[],
    )

    generator = ActionGenerator()

    actions = generator.generate(plan)

    assert actions == []
