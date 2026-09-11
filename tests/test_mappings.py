from app.providers.jusmar.mappings import (
    material_parameters,
    mesh_parameters,
    post_parameters,
    property_parameters,
)
from app.schemas.material import CreateMaterialRequest
from app.schemas.mesh import GenerateMeshRequest
from app.schemas.post_processing import PostProcessingRequest
from app.schemas.property import CreatePropertyRequest


def test_mesh_default_and_chinese_mapping():
    assert mesh_parameters(GenerateMeshRequest()) == {
        "mesh_type": "四面体网格",
        "element_order": "一阶单元",
        "mesh_size_mode": "级别设置",
        "mesh_density": "中",
        "mesh_option": "协调网格",
        "advanced_option_enable": False,
    }
    assert mesh_parameters(GenerateMeshRequest(mesh_density="high"))["mesh_density"] == "高"


def test_material_mapping_keeps_si_units_and_name():
    result = material_parameters(
        CreateMaterialRequest(material_name="测试材料", young_modulus=7e10)
    )
    assert result["young_modulus"] == 7e10
    assert result["material_name"] == "测试材料"
    assert result["constitutive_model"] == "线弹性"
    assert "extensions" not in result


def test_nested_property_and_output_identifiers():
    result = property_parameters(CreatePropertyRequest(material_ref="mat_123"))
    assert result["material_ref"] == "mat_123"
    assert result["advanced_option"] == {"gaussPointCount": "程序控制", "elementTech": "程序控制"}
    assert post_parameters(PostProcessingRequest())["output_items"] == [
        "modalDisplacement",
        "reactionForce",
    ]
