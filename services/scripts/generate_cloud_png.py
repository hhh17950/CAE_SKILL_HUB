import json
import sys


def generate_cloud_png(vtk_file, cloud_file_name, frequency):
    import vtk
    from vtk.util.numpy_support import vtk_to_numpy, numpy_to_vtk
    import numpy as np

    varname = "modal_displacement/mag"
    # 1.读取
    reader = vtk.vtkUnstructuredGridReader()
    reader.SetFileName(vtk_file)
    reader.ReadAllScalarsOn()
    reader.ReadAllVectorsOn()
    reader.Update()
    data = reader.GetOutput()

    vec = data.GetPointData().GetArray("modal_displacement/vector")
    if vec is None:
        raise RuntimeError("在点数据中找不到 modal_displacement/vector 数组")

    disp = vtk_to_numpy(vec)
    mag = np.sqrt((disp * disp).sum(axis=1))

    vtkmag = numpy_to_vtk(mag, deep=True)
    vtkmag.SetName("modal_displacement/mag")
    data.GetPointData().SetScalars(vtkmag)

    # 2.提取外表面
    surface = vtk.vtkDataSetSurfaceFilter()
    surface.SetInputData(data)
    surface.Update()
    surf = surface.GetOutput()

    # 3.获取着色标量的实际范围
    scal = surf.GetPointData().GetArray(varname)
    if scal is None:
        raise RuntimeError(f"标量数组 {varname} 在表面网格上不存在")
    smin, smax = scal.GetRange()

    # 4.创建色标查找表
    lut = vtk.vtkLookupTable()
    lut.SetNumberOfTableValues(256)
    lut.SetHueRange(0.667, 0.0)
    lut.SetSaturationRange(1.0, 1.0)
    lut.SetValueRange(1.0, 1.0)
    lut.SetRange(smin, smax)
    lut.Build()

    # 5.映射器
    mapper = vtk.vtkPolyDataMapper()
    mapper.SetInputData(surf)
    mapper.SetScalarModeToUsePointData()
    mapper.SelectColorArray(varname)
    mapper.SetScalarRange(smin, smax)
    mapper.SetLookupTable(lut)
    mapper.SetScalarVisibility(1)

    actor = vtk.vtkActor()
    actor.SetMapper(mapper)

    # 6.渲染器
    renderer = vtk.vtkRenderer()
    renderer.AddActor(actor)
    renderer.SetBackground(1.0, 1.0, 1.0)
    renderer.ResetCamera()

    cam = renderer.GetActiveCamera()
    cam.Elevation(25.0)
    cam.Azimuth(35.0)

    # 色标--右侧竖直
    scalar_bar = vtk.vtkScalarBarActor()
    scalar_bar.SetLookupTable(lut)
    scalar_bar.SetTitle(varname)
    scalar_bar.SetNumberOfLabels(6)
    scalar_bar.SetLabelFormat("%-8.4f")
    scalar_bar.SetOrientationToVertical()
    scalar_bar.SetPosition(0.86, 0.05)
    scalar_bar.SetWidth(0.10)
    scalar_bar.SetHeight(0.85)
    scalar_bar.SetTextPad(4)
    renderer.AddActor(scalar_bar)

    text_actor = vtk.vtkTextActor()
    text_actor.SetInput("Frequency: %g\nMax: %.6f\nMin: %.6f" % (frequency, smax, smin))
    text_actor.GetPositionCoordinate().SetCoordinateSystemToNormalizedDisplay()
    text_actor.SetPosition(0.02, 0.80)
    prop = text_actor.GetTextProperty()
    prop.SetFontSize(16)
    prop.SetJustificationToLeft()
    prop.SetVerticalJustificationToTop()
    prop.SetColor(0.1, 0.1, 0.1)
    prop.SetLineSpacing(1.4)
    renderer.AddActor(text_actor)

    # 7.离屏渲染窗口
    ren_win = vtk.vtkRenderWindow()
    ren_win.SetOffScreenRendering(1)
    ren_win.AddRenderer(renderer)
    ren_win.SetSize(1200, 800)
    ren_win.Render()

    # 8.保存成图片
    w2i = vtk.vtkWindowToImageFilter()
    w2i.SetInput(ren_win)
    w2i.SetInputBufferTypeToRGB()
    w2i.Update()

    writer = vtk.vtkPNGWriter()
    writer.SetFileName(cloud_file_name)
    writer.SetInputConnection(w2i.GetOutputPort())
    writer.Write()
    print(f"已保存：{cloud_file_name}")


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        print("用法：python generate_cloud_png.py <cloud_info.json>", file=sys.stderr)
        return 2
    cloud_info_path = argv[0]
    with open(cloud_info_path, encoding="utf-8") as f:
        cloud_info = json.load(f)
    for key, value in cloud_info.items():
        vtk_file = value["vtk_file"]
        cloud_file_name = value["cloud_file_name"]
        frequency = value["frequency"]
        generate_cloud_png(vtk_file, cloud_file_name, frequency)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
