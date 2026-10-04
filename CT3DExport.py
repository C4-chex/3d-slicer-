# -*- coding: utf-8 -*-
"""Local review exports for CT3D; no DICOM identity metadata is read here."""

import math
import os

import slicer
import vtk
from .CT3DViewLabels import isReviewLabel


def _number(value):
    value = float(value)
    return value if math.isfinite(value) else None


def _numbers(values):
    return [_number(value) for value in values]


def _matrix(matrix):
    return [[_number(matrix.GetElement(row, column)) for column in range(4)]
            for row in range(4)]


def captureReviewImage(imagePath, parameterCollector=None):
    """Capture CT3D's four views through Slicer's ScreenCapture implementation.

    The caller must check burned-in-annotation metadata and obtain the user's
    visual review before calling. Hiding overlays does not remove text baked
    into image pixels, nor establish that the image is de-identified.

    This function does not change layout or camera. It requires CT3D layout
    501 and restores temporary display changes even if capture raises.
    Prefer a new temporary PNG path; the caller owns overwrite/atomic commit.
    """
    from ScreenCapture import ScreenCaptureLogic

    if not str(imagePath).lower().endswith(".png"):
        raise ValueError("CT3D 四视图截图需要 PNG 文件路径。")
    layoutManager = slicer.app.layoutManager()
    if not layoutManager or layoutManager.layout != 501:
        raise RuntimeError("请先恢复 CT3D 四视图布局，再导出截图。")
    sliceWidgets = [layoutManager.sliceWidget(name)
                    for name in ("Red", "Yellow", "Green")]
    if any(widget is None for widget in sliceWidgets) or layoutManager.threeDViewCount < 1:
        raise RuntimeError("四视图尚未准备好，请先载入影像。")

    views = [widget.sliceView() for widget in sliceWidgets]
    controllers = [widget.sliceController() for widget in sliceWidgets]
    for index in range(layoutManager.threeDViewCount):
        widget = layoutManager.threeDWidget(index)
        views.append(widget.threeDView())
        controllers.append(widget.threeDController())

    restorations = []
    hiddenActors = set()
    annotations = None
    annotationsEnabled = None
    textClasses = (
        "vtkCornerAnnotation", "vtkTextActor", "vtkTextActor3D",
        "vtkBillboardTextActor3D", "vtkCaptionActor2D", "vtkScalarBarActor",
    )

    def temporarilySet(setter, original, temporary):
        # Register before mutation so a partial failure is also restorable.
        restorations.append((setter, original))
        setter(temporary)

    def hideTextActors():
        # Never read the text: the only saved state is each actor's visibility.
        for view in views:
            renderers = view.renderWindow().GetRenderers()
            for rendererIndex in range(renderers.GetNumberOfItems()):
                props = renderers.GetItemAsObject(rendererIndex).GetViewProps()
                for propIndex in range(props.GetNumberOfItems()):
                    actor = props.GetItemAsObject(propIndex)
                    if isReviewLabel(actor):
                        continue
                    if any(actor.IsA(className) for className in textClasses):
                        key = actor.GetAddressAsString("")
                        if key not in hiddenActors:
                            hiddenActors.add(key)
                            temporarilySet(actor.SetVisibility, actor.GetVisibility(), False)

    try:
        # DataProbe's callback reads this flag before rebuilding its corner text.
        # Avoid updateSliceViewFromGUI(), which also changes observer registration.
        dataProbe = getattr(slicer.modules, "DataProbeInstance", None)
        infoWidget = getattr(dataProbe, "infoWidget", None)
        annotations = getattr(infoWidget, "sliceAnnotations", None)
        if annotations is not None:
            annotationsEnabled = annotations.sliceViewAnnotationsEnabled
            annotations.sliceViewAnnotationsEnabled = 0

        for controller in controllers:
            temporarilySet(controller.setVisible, not controller.isHidden(), False)

        # Markup labels can contain arbitrary user text. Keep the geometry visible.
        for display in slicer.util.getNodesByClass("vtkMRMLMarkupsDisplayNode"):
            temporarilySet(display.SetPointLabelsVisibility,
                           display.GetPointLabelsVisibility(), False)
            temporarilySet(display.SetPropertiesLabelVisibility,
                           display.GetPropertiesLabelVisibility(), False)

        # Explicitly hide the slice corner actor even before its first render.
        for widget in sliceWidgets:
            actor = widget.sliceView().cornerAnnotation()
            key = actor.GetAddressAsString("")
            hiddenActors.add(key)
            temporarilySet(actor.SetVisibility, actor.GetVisibility(), False)

        slicer.app.processEvents()
        slicer.util.forceRenderAllViews()
        hideTextActors()
        ScreenCaptureLogic().captureImageFromView(None, str(imagePath))
        if not os.path.isfile(imagePath) or os.path.getsize(imagePath) == 0:
            raise RuntimeError("Slicer 未能写出四视图 PNG，请检查目标目录。")
        # Record the geometry while the captured layout is still in effect.
        return parameterCollector() if parameterCollector else None
    finally:
        restoreFailed = False
        for setter, original in reversed(restorations):
            try:
                setter(original)
            except Exception:
                # Restore the other independent settings even if a node was removed.
                restoreFailed = True
        if annotations is not None:
            annotations.sliceViewAnnotationsEnabled = annotationsEnabled
        slicer.util.forceRenderAllViews()
        if restoreFailed:
            raise RuntimeError("截图期间场景发生变化，部分显示设置未能恢复；请重新打开四视图。")


def reviewDisplayParameters(widget):
    """Return an identity-free numeric display record, not a restorable scene.

    Uses currentVolume/currentVRDisplay, never the pending series selection.
    Does not serialize node names, IDs, UIDs, DICOM fields or source paths.
    Nonlinear transforms and segmentation voxels are deliberately not embedded;
    a Slicer scene is required to resume the complete spatial editing context.
    """
    volume = getattr(widget, "currentVolume", None)
    if volume is None or volume.GetImageData() is None:
        raise RuntimeError("请先载入影像，再导出视图参数。")
    layoutManager = slicer.app.layoutManager()
    imageData = volume.GetImageData()
    display = volume.GetDisplayNode()
    ijkToRas = vtk.vtkMatrix4x4()
    volume.GetIJKToRASMatrix(ijkToRas)
    parentTransform = volume.GetParentTransformNode()
    transform = {"present": parentTransform is not None,
                 "linear_to_world": None, "local_ras_to_world_ras_matrix": None}
    if parentTransform is not None:
        transform["linear_to_world"] = bool(parentTransform.IsTransformToWorldLinear())
        if transform["linear_to_world"]:
            toWorld = vtk.vtkMatrix4x4()
            if parentTransform.GetMatrixTransformToWorld(toWorld):
                transform["local_ras_to_world_ras_matrix"] = _matrix(toWorld)

    slices = {}
    for name in ("Red", "Yellow", "Green"):
        sliceWidget = layoutManager.sliceWidget(name)
        if sliceWidget is None:
            continue
        logic = sliceWidget.sliceLogic()
        node = logic.GetSliceNode()
        orientation = node.GetOrientationString()
        slices[name] = {
            "orientation": orientation if orientation in ("Axial", "Coronal", "Sagittal") else "Custom",
            "slice_to_world_ras_matrix": _matrix(node.GetSliceToRAS()),
            "slice_offset_mm": _number(logic.GetSliceOffset()),
            "field_of_view_mm": _numbers(node.GetFieldOfView()),
            "slice_visible_in_3d": bool(node.GetSliceVisible()),
        }

    rendering = {"visible": False, "cropping_enabled": False, "roi": None,
                 "transfer_functions": None}
    vrDisplay = getattr(widget, "currentVRDisplay", None)
    if vrDisplay is not None:
        rendering["visible"] = bool(vrDisplay.GetVisibility())
        rendering["cropping_enabled"] = bool(vrDisplay.GetCroppingEnabled())
        roiGetter = getattr(vrDisplay, "GetMarkupsROINode", None)
        roi = roiGetter() if roiGetter is not None else None
        if roi is None:
            roiGetter = getattr(vrDisplay, "GetROINode", None)
            roi = roiGetter() if roiGetter is not None else None
        if roi is not None:
            roiRecord = {"object_to_world_ras_matrix": None, "size_object_mm": None,
                         "parent_transform_present": roi.GetParentTransformNode() is not None}
            matrixGetter = getattr(roi, "GetObjectToWorldMatrix", None)
            if matrixGetter is not None:
                try:
                    objectToWorld = matrixGetter()
                except TypeError:
                    objectToWorld = vtk.vtkMatrix4x4()
                    matrixGetter(objectToWorld)
                if objectToWorld is not None:
                    roiRecord["object_to_world_ras_matrix"] = _matrix(objectToWorld)
            sizeGetter = getattr(roi, "GetSize", None)
            if sizeGetter is not None:
                try:
                    size = sizeGetter()
                except TypeError:
                    size = [0.0, 0.0, 0.0]
                    sizeGetter(size)
                roiRecord["size_object_mm"] = _numbers(size)
            elif hasattr(roi, "GetRadiusXYZ"):
                radius = [0.0, 0.0, 0.0]
                roi.GetRadiusXYZ(radius)
                roiRecord["size_object_mm"] = _numbers(2 * value for value in radius)
            rendering["roi"] = roiRecord

        propertyNode = vrDisplay.GetVolumePropertyNode()
        if propertyNode is not None:
            prop = propertyNode.GetVolumeProperty()
            transfers = {}
            for name, function, width in (
                    ("scalar_opacity_x_value_midpoint_sharpness", prop.GetScalarOpacity(0), 4),
                    ("gradient_opacity_x_value_midpoint_sharpness", prop.GetGradientOpacity(0), 4),
                    ("color_x_rgb_midpoint_sharpness", prop.GetRGBTransferFunction(0), 6)):
                points = []
                for index in range(function.GetSize()):
                    values = [0.0] * width
                    function.GetNodeValue(index, values)
                    points.append(_numbers(values))
                transfers[name] = points
            transfers["component"] = 0
            transfers["shade"] = bool(prop.GetShade(0))
            rendering["transfer_functions"] = transfers

    cameraRecord = None
    if layoutManager.threeDViewCount > 0:
        view = layoutManager.threeDWidget(0).threeDView()
        camera = view.renderWindow().GetRenderers().GetFirstRenderer().GetActiveCamera()
        cameraRecord = {
            "position_world_ras_mm": _numbers(camera.GetPosition()),
            "focal_point_world_ras_mm": _numbers(camera.GetFocalPoint()),
            "view_up": _numbers(camera.GetViewUp()),
            "parallel_projection": bool(camera.GetParallelProjection()),
            "parallel_scale_mm": _number(camera.GetParallelScale()),
            "view_angle_degrees": _number(camera.GetViewAngle()),
        }

    return {
        "schema_version": 2,
        "scope": "显示参数记录；不包含影像、分割或非线性变换数据，不能替代可恢复的 Slicer 场景。",
        "restorable_scene": False,
        "layout_id": int(layoutManager.layout),
        "plane_mode": "acquisition_aligned" if widget.planeCombo.currentIndex == 1 else "anatomical_mpr",
        "slice_interpolation": "linear" if display and display.GetInterpolate() else "nearest",
        "volume_rendering_interpolation": (
            {0: "nearest", 1: "linear"}.get(vrDisplay.GetVolumePropertyNode().GetVolumeProperty().GetInterpolationType(), "unknown")
            if vrDisplay and vrDisplay.GetVolumePropertyNode() else None),
        "common_slice_scale_enabled": bool(widget.commonScaleCheck.checked),
        "volume": {
            "voxel_dimensions_ijk": [int(value) for value in imageData.GetDimensions()],
            "spacing_mm": _numbers(volume.GetSpacing()),
            "ijk_to_local_ras_matrix": _matrix(ijkToRas),
            "parent_transform": transform,
            "window_level": {"auto": bool(display.GetAutoWindowLevel()),
                             "window": _number(display.GetWindow()),
                             "level": _number(display.GetLevel())} if display is not None else None,
        },
        "slice_views": slices,
        "volume_rendering": rendering,
        "camera": cameraRecord,
    }
