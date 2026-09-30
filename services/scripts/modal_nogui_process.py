import json
import os
import shutil
import subprocess
import sys
from datetime import datetime
import jusmarapp


def get_params():
    """
    从环境变量读取所有参数
    """

    params = {}
    # (环境变量名，默认值，转换函数)
    spec = [
        ("GUIE_PROJECT_DIR", "/home/vinci/Downloads/719-agent/noguie-test", str),
        ("GUIE_JUSMAR_LOG", "/home/vinci/Downloads/719-agent/log", str),
        ("GUIE_CLOUD_INFO_DIR", "/home/vinci/Downloads/719-agent/cloud_info.json", str),
        ("GUIE_MODEL_PATH", None, str),
        ("GUIE_YOUNG_MODULUS", 2e+11, float),
        ("GUIE_POISSON_TATIO", 0.3, float),
        ("GUIE_DENSITY", 7850, int),
        ("GUIE_NUMBER_OF_ROOTS", 10, int),
    ]
    for env_name, default, cast in spec:
        raw = os.getenv(env_name)
        if raw if None:
            if env_name == "GUIE_NODEL_PATH":
                raise ValueError("模型路径不存在!")
            else:
                params[env_name] = default
        else:
            params[env_name] = cast(raw)
    return params


def check_result(result, step_name, mode="done_or_not_none", success_values=("done",)):
    """
    检查结果是否成功，成功则打印 INFO，失败则打印 ERROR 并抛出异常。

    参数:
        result: 要检查的结果
        step_name: 步骤名称，例如：新增属性表
        mode: 判断模式
            - "truthy": result 为真值时成功
            - "done": result 等于 success_values 中的值时成功
              默认 success_values=("done",)
              对应：if result != "done": 失败
            - "done_or_not_none": result 为 "done" 或非 None 时成功
        success_values: 明确的成功值，默认（"done",）

    返回：
        成功时返回 result
    """

    if mode == "truthy":
        ok = bool(result)

    elif mode == "done":
        ok = result in success_values

    elif mode == "done_or_not_none":
        ok = result in success_values or result is not None

    else:
        raise ValueError(f"不支持的 mode: {mode}")

    if ok:
        print(
            f'{datetime.now().strftime("%Y-%m-%d %H:%M:%S")}\t'
            f"[INFO]\t【{step_name}】{step_name}成功！"
        )
        print(f'{datetime.now().strftime("%Y-%m-%d %H:%M:%S")}\t' f'[INFO]\t{"=" * 80}')
        return result

    print(
        f'{datetime.now().strftime("%Y-%m-%d %H:%M:%S")}\t'
        f"[ERROR]\t【{step_name}】{step_name}失败！ result={result!r}"
    )
    raise Exception(f"【{step_name}】{step_name}失败！ result={result!r}")


def converter_jolly(project_path):
    """
        将jolly数据转换为vtk数据
    """
    converter_path = os.path.join(os.path.dirname(sys.executable), "Jolly2VtkConverter")
    simulation_path = os.path.join(project_path, "project/new_device_1/design_sim_instance_0/simulation_1")
    info_json_path = os.path.join(simulation_path, "sim-database/info.json")
    cwd = simulation_path
    output_path = "vtk_data_output"
    cmd = [
        converter_path,
        "-i",
        info_json_path,
        "-o",
        output_path,
    ]

    result = subprocess.run(cmd, capture_output=True, text=True, cwd=cwd)
    if result.returncode == 0:
        print(
            f'{datetime.now().strftime("%Y-%m-%d %H:%M:%S")}\t'
            f"[INFO]\t【vtk数据转换】vtk数据转换成功!"
        )
        print(result.stdout)
        vtk_data_path = os.path.join(simulation_path, output_path)
        return vtk_data_path
    else:
        print(
            f'{datetime.now().strftime("%Y-%m-%d %H:%M:%S")}\t'
            f"[ERROR]\t【vtk数据转换】vtk数据转换失败! result={result.stderr!r}"
        )
        raise Exception(f"【vtk数据转换】vtk数据转换失败! result={result.stderr!r}")


def main():
    params = get_params()
    # 工程路径
    project_dir = os.path.abspath(params["GUIE_PROJECT_DIR"])
    # 模型文件路径
    model_path = os.path.abspath(params["GUIE_MODEL_PATH"])
    if not os.path.exists(model_path):
        raise FileNotFoundError(f"模型文件不存在：{model_path}")
    # 杨氏模量
    young_modulus = params["GUIE_YOUNG_MODULUS"]
    # 泊松比
    poisson_ratio = params["GUIE_POISSON_TATIO"]
    # 密度
    density = params["GUIE_DENSITY"]
    # 模态阶数
    number_of_roots = params["GUIE_NUMBER_OF_ROOTS"]

    print(f'{datetime.now().strftime("%Y-%m-%d %H:%M:%S")}\t' f'[INFO]\t{"=" * 80}')
    print(
        f'{datetime.now().strftime("%Y-%m-%d %H:%M:%S")}\t' f'[INFO]\t{"=" * 36}开始运行{"=" * 36}'
    )
    print(f'{datetime.now().strftime("%Y-%m-%d %H:%M:%S")}\t' f'[INFO]\t{"=" * 80}')
    if os.path.exists(project_dir):
        shutil.rmtree(project_dir)

    # 创建工程
    my_project = jusmarapp.data.create_project(project_dir)
    check_result(my_project, "创建工程", "truthy")

    # 打开装置
    default_device_name = my_project.devices_name()[0]
    my_device = my_project.device(default_device_name)
    my_simulation = my_device.simdb().simulation(0)

    # 1.导入几何模型
    my_modeling = jusmarapp.service.modeling(my_simulation)
    is_success = my_modeling.import_geometry(model_path)
    check_result(is_success, "导入模型", "truthy")

    # 2.生成网格，采用四面体网格尺寸
    tri_mesher = my_modeling.mesher().free_tet_mesher()
    all_bds = my_simulation.geometry().solid(0).bodies()
    newpids = tri_mesher.generate_mesh(all_bds)
    check_result(newpids, "生成四面体网格", "truthy")

    # 3.新增材料表
    create_isotropic_group = my_simulation.create_datagroup(
        "/Equation_config/physics/material_list/isotropic", {}
    )
    check_result(create_isotropic_group, "新增材料表", "truthy")

    isotropic_path = create_isotropic_group.path()
    # 3.1设置材料表杨氏模量
    young_modulus_result = my_simulation.set_dataitem(
        os.path.join(isotropic_path, "young_modulus"), young_modulus
    )
    check_result(young_modulus_result, "设置材料表杨氏模量", "done")

    # 3.2设置材料表泊松比值
    poisson_ratio_result = my_simulation.set_dataitem(
        os.path.join(isotropic_path, "poisson_ratio"), poisson_ratio
    )
    check_result(poisson_ratio_result, "设置材料表泊松比值", "done")

    # 3.3设置材料表密度值
    density_result = my_simulation.set_dataitem(
        os.path.join(isotropic_path, "density"), density
    )
    check_result(density_result, "设置材料表密度值", "done")

    # 4.新增属性表
    create_property_3d_group = my_simulation.create_datagroup(
        "/Equation_config/physics/property_list/property_3D", {}
    )
    check_result(create_property_3d_group, "新增属性表", "truthy")
    property_3d_path = create_property_3d_group.path()

    # 4.1设置属性表实体集
    set_cell_result = my_simulation.set_dataitem(
        os.path.join(property_3d_path, "cell_id", newpids)
    )
    check_result(set_cell_result, "设置属性表实体集", "done")

    # 5.设置求解类型
    pde_solver_result = my_simulation.set_dataitem(
        "/Equation_config/analyses/analyses_1/pde_solver/type", "modal"
    )
    check_result(pde_solver_result, "设置求解类型", "done")

    # 6.定义模态阶数
    number_of_roots_option_result = my_simulation.set_dataitem(
        "/Equation_config/analyses/analyses_1/subcase_def/subcase_1/subcase_set/number_of_roots_option",
        True,
    )
    check_result(number_of_roots_option_result, "定义模态阶数", "done")

    # 6.1设置工况模态阶数
    number_of_roots_result = my_simulation.set_dataitem(
        "/Equation_config/analyses/analyses_1/subcase_def/subcase_1/subcase_set/number_of_roots",
        number_of_roots,
    )
    check_result(number_of_roots_result, "设置工况模态阶数", "done")

    # 7.设置模态位移
    eigenvectors_option_result = my_simulation.set_dataitem(
        "/Equation_config/analyses/analyses_1/subcase_def/subcase_1/postprocessor_set/eigenvectors_option",
        True,
    )
    check_result(eigenvectors_option_result, "设置模态位移", "done")

    # 8.生成计算文件
    generate_result = my_modeling.generate_simulation_input()
    check_result(generate_result, "生成计算文件", "truthy")

    # 9.开始运行模拟
    start_simulation_result = my_modeling.start_simulation()
    if not start_simulation_result:
        print(f'{datetime.now().strftime("%Y-%m-%d %H:%M:%S")}\t[ERROR]\t【运行模拟】运行模拟执行失败')
        return
    print(f'{datetime.now().strftime("%Y-%m-%d %H:%M:%S")}\t[INFO]\t【运行模拟】运行模拟执行开始')
    # 10.等待运行模拟的结果，求解器的标准输出将输出到传入的文件路径里
    run_status = my_modeling.wait_simulation(params["GUIE_JUSMAR_LOG"])
    print(
        f'{datetime.now().strftime("%Y-%m-%d %H:%M:%S")}\t[INFO]\t【运行模拟】运行模拟结束：{run_status}'
    )

    print(f'{datetime.now().strftime("%Y-%m-%d %H:%M:%S")}\t' f'[INFO]\t{"=" * 80}')
    print(
        f'{datetime.now().strftime("%Y-%m-%d %H:%M:%S")}\t' f'[INFO]\t{"=" * 36}开始结束{"=" * 36}'
    )
    print(f'{datetime.now().strftime("%Y-%m-%d %H:%M:%S")}\t' f'[INFO]\t{"=" * 80}')

    my_project.save()
    # 11.jolly数据转换vtk数据
    vtk_data_path = converter_jolly(project_dir)

    # 12.生成云图
    vtk_files = []
    cloud_json_info = {}
    for root, dirs, files in os.walk(vtk_data_path):
        for f in files:
            if f.endswith(".vtk"):
                vtk_files.append(os.path.abspath(os.path.join(root, f)))
    cloud_path = os.path.join(os.path.dirname(vtk_data_path), "cloud_png")
    if not os.path.exists(cloud_path):
        os.mkdir(cloud_path)
    for index, vtk_file in enumerate(vtk_files):
        cloud_file_name = os.path.join(cloud_path, f"cloud_3d_{index+1}.png")
        cloud_json_info.update({
            index+1: {
                "vtk_file": vtk_file,
                "cloud_file_name": cloud_file_name
            }
        })

    # 13.查看结果数据
    collections = my_device.resdb().result().collection(0)
    frames = collections.frames()
    for frame in frames:
        index = frame.info("step_number")
        frequency = frame.info("time")
        cloud_json_info[int(index)]["frequency"] = float(frequency)
    # 14.将结果写入json文件
    with open(params["GUIE_CLOUD_INFO_DIR"], "w", encoding="utf-8") as f:
        json.dump(cloud_json_info, f, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(e)
    finally:
        exit(0)
