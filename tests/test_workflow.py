from tests.legacy_client import run_workflow


async def test_nine_steps_through_public_api(client):
    result = await run_workflow(client, b"Mock-only test geometry")
    assert result["passed"]
    assert result["business_steps"] == 9
    assert result["task_status"] == "succeeded"
    assert len(result["requests"]) == 10  # upload plus nine business calls


async def test_openapi_has_typed_nine_operations(app):
    schema = app.openapi()
    expected = {
        "import_geometry",
        "generate_mesh",
        "create_material",
        "create_3d_property",
        "add_load_case",
        "solver_settings",
        "case_settings",
        "post_processing_settings",
        "submit_simulation",
    }
    operations = {
        operation["operationId"]: operation
        for path in schema["paths"].values()
        for operation in path.values()
        if isinstance(operation, dict) and "operationId" in operation
    }
    assert expected.issubset(operations)
    for name in expected:
        operation = operations[name]
        assert "requestBody" in operation
        assert "security" in operation
        assert "422" in operation["responses"]
    assert schema["components"]["schemas"]["GenerateMeshRequest"]["additionalProperties"] is False
