# -*- coding: utf-8 -*-
"""Small review controls backed by Slicer's existing MRML display nodes."""

import math

import ctk
import qt
import slicer
import vtk
from .CT3DViewLabels import ReviewLabels


class ReviewControlsMixin:
    def setupReviewControls(self, root):
        self._observedDisplay = None
        self._displayObserver = None
        self._initialCamera = None
        self._viewLabels = None
        self.loadedLabel = qt.QLabel("正在显示：尚未载入影像")
        self.loadedLabel.setWordWrap(True)
        self.loadedLabel.setStyleSheet("padding:6px; border:1px solid #888;")
        root.addWidget(self.loadedLabel)

        self.reviewBox = ctk.ctkCollapsibleButton()
        self.reviewBox.text = "阅片 · 定位与显示"
        root.addWidget(self.reviewBox)
        layout = qt.QGridLayout(self.reviewBox)
        self.softButton = qt.QPushButton("CT 软组织")
        self.lungButton = qt.QPushButton("CT 肺")
        self.boneButton = qt.QPushButton("CT 骨")
        self.autoButton = qt.QPushButton("自动对比度")
        for col, button in enumerate((self.softButton, self.lungButton, self.boneButton)):
            layout.addWidget(button, 0, col)
        self.softButton.clicked.connect(lambda: self.applyWindowLevel(400, 40))
        self.lungButton.clicked.connect(lambda: self.applyWindowLevel(1500, -600))
        self.boneButton.clicked.connect(lambda: self.applyWindowLevel(2000, 400))
        self.autoButton.clicked.connect(self.applyAutoWindowLevel)

        self.windowSpin = qt.QDoubleSpinBox()
        self.windowSpin.setDecimals(2)
        self.windowSpin.setRange(0.01, 1e12)
        self.windowSpin.setKeyboardTracking(False)
        self.levelSpin = qt.QDoubleSpinBox()
        self.levelSpin.setDecimals(2)
        self.levelSpin.setRange(-1e12, 1e12)
        self.levelSpin.setKeyboardTracking(False)
        layout.addWidget(qt.QLabel("窗宽"), 1, 0)
        layout.addWidget(self.windowSpin, 1, 1, 1, 2)
        layout.addWidget(qt.QLabel("窗位"), 2, 0)
        layout.addWidget(self.levelSpin, 2, 1, 1, 2)
        self.windowSpin.editingFinished.connect(self.applyManualWindowLevel)
        self.levelSpin.editingFinished.connect(self.applyManualWindowLevel)
        layout.addWidget(self.autoButton, 3, 0)
        self.intensityLabel = qt.QLabel("")
        self.intensityLabel.setWordWrap(True)
        layout.addWidget(self.intensityLabel, 3, 1, 1, 2)

        self.crosshairCheck = qt.QCheckBox("十字线联动定位")
        self.crosshairCheck.checked = True
        self.crosshairCheck.toggled.connect(self.onCrosshairToggled)
        layout.addWidget(self.crosshairCheck, 4, 0, 1, 3)
        self.slicePlanesCheck = qt.QCheckBox("在三维中显示切片平面")
        self.slicePlanesCheck.toggled.connect(self.onSlicePlanesToggled)
        layout.addWidget(self.slicePlanesCheck, 5, 0, 1, 3)
        self.resetViewButton = qt.QPushButton("复位四视图")
        self.resetViewButton.clicked.connect(self.onResetReviewViews)
        layout.addWidget(self.resetViewButton, 6, 0, 1, 3)
        hint = qt.QLabel("切片滚轮逐层浏览；按住 Shift 移动鼠标可联动定位。复位恢复方向、缩放和居中，保留窗宽窗位与裁剪。")
        hint.setWordWrap(True)
        layout.addWidget(hint, 7, 0, 1, 3)
        self.planeCombo = qt.QComboBox()
        self.planeCombo.addItem("标准解剖方向（MPR 重建）")
        self.planeCombo.addItem("按原始采集方向对齐")
        self.planeCombo.currentIndexChanged.connect(self.onPlaneModeChanged)
        layout.addWidget(self.planeCombo, 8, 0, 1, 3)
        self.interpolateCheck = qt.QCheckBox("二维平滑显示（关闭后查看最近邻采样）")
        self.interpolateCheck.checked = True
        self.interpolateCheck.toggled.connect(self.applyDisplayInterpolation)
        layout.addWidget(self.interpolateCheck, 9, 0, 1, 3)
        self.resolutionLabel = qt.QLabel("")
        self.resolutionLabel.setWordWrap(True)
        layout.addWidget(self.resolutionLabel, 10, 0, 1, 3)
        self.commonScaleCheck = qt.QCheckBox("三切面按相同比例显示")
        self.commonScaleCheck.checked = True
        self.commonScaleCheck.toggled.connect(self.onResetReviewViews)
        layout.addWidget(self.commonScaleCheck, 11, 0, 1, 3)

        self.volumeBox = ctk.ctkCollapsibleButton()
        self.volumeBox.text = "三维 · 显示与裁剪"
        root.addWidget(self.volumeBox)
        volumeLayout = qt.QVBoxLayout(self.volumeBox)
        self.renderCheck = qt.QCheckBox("显示三维体渲染")
        self.renderCheck.checked = True
        self.renderCheck.toggled.connect(self.onRenderToggled)
        volumeLayout.addWidget(self.renderCheck)
        self.renderPresetCombo = qt.QComboBox()
        self.renderPresetCombo.addItem("跟随二维对比度", "")
        self.renderPresetCombo.currentIndexChanged.connect(self.onRenderPresetChanged)
        volumeLayout.addWidget(self.renderPresetCombo)
        self.cropCheck = qt.QCheckBox("仅显示裁剪框内的体影像")
        self.cropCheck.toggled.connect(self.onCropToggled)
        volumeLayout.addWidget(self.cropCheck)
        self.roiVisibleCheck = qt.QCheckBox("显示裁剪框与拖动手柄")
        self.roiVisibleCheck.toggled.connect(self.onROIVisibilityToggled)
        volumeLayout.addWidget(self.roiVisibleCheck)
        row = qt.QHBoxLayout()
        self.fitROIButton = qt.QPushButton("恢复完整范围")
        self.fitROIButton.clicked.connect(self.onFitROI)
        row.addWidget(self.fitROIButton)
        self.centerROIButton = qt.QPushButton("框中心移至十字线")
        self.centerROIButton.clicked.connect(self.onCenterROI)
        row.addWidget(self.centerROIButton)
        volumeLayout.addLayout(row)
        hint = qt.QLabel("拖动框的面或手柄调整范围。裁剪只控制体渲染显示；原始切片与分割模型保持完整。")
        hint.setWordWrap(True)
        volumeLayout.addWidget(hint)
        self.reviewBox.enabled = False
        self.volumeBox.enabled = False

    @staticmethod
    def nodeIsPresent(node):
        return bool(node and node.GetID() and slicer.mrmlScene.GetNodeByID(node.GetID()) == node)

    def detachDisplayObserver(self):
        if self._observedDisplay and self._displayObserver is not None:
            self._observedDisplay.RemoveObserver(self._displayObserver)
        self._observedDisplay = None
        self._displayObserver = None

    def updateReviewState(self):
        if hasattr(self, "measurementBox"):
            self.updateMeasurementState()
        loaded = self.nodeIsPresent(self.currentVolume) and bool(self.loadedQuality)
        self.reviewBox.enabled = loaded
        self.volumeBox.enabled = loaded and self.nodeIsPresent(self.currentVRDisplay)
        self.exportViewButton.enabled = loaded
        self.editSegmentationButton.enabled = loaded
        hasSeg = loaded and self.nodeIsPresent(self.currentSegmentation)
        self.updateSurfaceButton.enabled = hasSeg
        self.saveSegmentationButton.enabled = hasSeg
        self.exportMeshButton.enabled = hasSeg and bool(self.nonEmptySegmentIDs())
        self.editSegmentationButton.text = "继续编辑当前分割" if hasSeg else "创建结构并开始分割"
        if not loaded:
            self.detachDisplayObserver()
            self.loadedLabel.text = "正在显示：尚未载入影像"
            return
        info = self.loadedQuality
        selectedOther = self.currentQuality and (
            self.currentQuality["uid"] != info["uid"]
            or set(self.currentQuality["files"]) != set(info["files"]))
        self.loadedLabel.text = (
            f"正在显示：{info.get('displayCode', '')} · {info['modality']} · {len(info['files'])} 张"
            + ("\n候选序列已改变；点击“载入选中序列”才会切换影像。" if selectedOther else "")
        )
        ct = info["modality"] == "CT" and info["huEligible"]
        for button in (self.softButton, self.lungButton, self.boneButton):
            button.enabled = ct
        display = self.currentVolume.GetDisplayNode()
        if not display:
            self.detachDisplayObserver()
            self.reviewBox.enabled = False
            return
        if self._observedDisplay != display:
            self.detachDisplayObserver()
            self._observedDisplay = display
            self._displayObserver = display.AddObserver(vtk.vtkCommand.ModifiedEvent, self.syncWindowLevel)
        self.syncWindowLevel()
        self.syncRenderingControls()
        spacing = self.currentVolume.GetSpacing()
        ratio = max(spacing) / min(spacing)
        self.resolutionLabel.text = (
            "采集体素间距：" + " × ".join(f"{v:.3g}" for v in spacing) + " mm。"
            + (f"最长轴间距约为最短轴的 {ratio:.1f} 倍；重建面与3D的细节受采样限制。" if ratio > 2 else "")
            + "三维采用线性插值改善显示，不增加真实细节。"
        )

    def syncWindowLevel(self, *_):
        if not self.nodeIsPresent(self.currentVolume) or not self.loadedQuality:
            return
        display = self.currentVolume.GetDisplayNode()
        if not display:
            return
        self.interpolateCheck.blockSignals(True)
        self.interpolateCheck.checked = bool(display.GetInterpolate())
        self.interpolateCheck.blockSignals(False)
        for spin, value in ((self.windowSpin, display.GetWindow()), (self.levelSpin, display.GetLevel())):
            if not spin.hasFocus():
                spin.blockSignals(True)
                spin.value = value
                spin.blockSignals(False)
        unit = ("HU（CT 元数据声明）" if self.loadedQuality["huEligible"] else
                "MR 强度（非 HU）" if self.loadedQuality["modality"] == "MR" else "强度单位未确认")
        self.intensityLabel.text = ("自动 · " if display.GetAutoWindowLevel() else "手动 · ") + unit

    def applyManualWindowLevel(self):
        if not self.nodeIsPresent(self.currentVolume):
            return
        width, level = float(self.windowSpin.value), float(self.levelSpin.value)
        if not math.isfinite(width) or not math.isfinite(level) or width <= 0:
            return
        display = self.currentVolume.GetDisplayNode()
        modifying = display.StartModify()
        display.SetAutoWindowLevel(False)
        display.SetWindowLevel(width, level)
        display.EndModify(modifying)

    def crosshairNode(self):
        node = slicer.mrmlScene.GetFirstNodeByClass("vtkMRMLCrosshairNode")
        return node or slicer.mrmlScene.AddNewNodeByClass("vtkMRMLCrosshairNode")

    def onCrosshairToggled(self, enabled):
        if not self.nodeIsPresent(self.currentVolume):
            return
        node = self.crosshairNode()
        node.SetCrosshairMode(node.ShowBasic if enabled else node.NoCrosshair)
        node.SetCrosshairBehavior(node.CenteredJumpSlice if enabled else node.NoAction)
        for name in ("Red", "Yellow", "Green"):
            widget = slicer.app.layoutManager().sliceWidget(name)
            if widget:
                display = widget.sliceLogic().GetSliceDisplayNode()
                if display:
                    display.SetIntersectingSlicesVisibility(bool(enabled))

    def onSlicePlanesToggled(self, enabled):
        if not self.nodeIsPresent(self.currentVolume):
            return
        for name in ("Red", "Yellow", "Green"):
            widget = slicer.app.layoutManager().sliceWidget(name)
            if widget:
                widget.mrmlSliceNode().SetSliceVisible(bool(enabled))

    def rememberCamera(self):
        camera = slicer.modules.cameras.logic().GetViewActiveCameraNode(
            slicer.app.layoutManager().threeDWidget(0).mrmlViewNode()).GetCamera()
        self._initialCamera = vtk.vtkCamera()
        self._initialCamera.DeepCopy(camera)

    def onResetReviewViews(self, *_):
        if not self.nodeIsPresent(self.currentVolume):
            return
        manager = slicer.app.layoutManager()
        manager.setLayout(501)
        transform = self.currentVolume.GetParentTransformNode()
        if transform and not transform.IsTransformToWorldLinear() and self.planeCombo.currentIndex == 1:
            self.planeCombo.blockSignals(True)
            self.planeCombo.setCurrentIndex(0)
            self.planeCombo.blockSignals(False)
        for name, orientation in (("Red", "Axial"), ("Yellow", "Coronal"), ("Green", "Sagittal")):
            manager.sliceWidget(name).mrmlSliceNode().SetOrientation(orientation)
        slicer.util.setSliceViewerLayers(background=self.currentVolume, foreground=None, label=None)
        if self.planeCombo.currentIndex == 1:
            for name in ("Red", "Yellow", "Green"):
                manager.sliceWidget(name).mrmlSliceNode().RotateToVolumePlane(self.currentVolume)
        matrix = vtk.vtkMatrix4x4()
        self.currentVolume.GetIJKToRASMatrix(matrix)
        dimensions = self.currentVolume.GetImageData().GetDimensions()
        # In acquisition-aligned mode start at real voxel centers, not halfway between slices.
        ijk = [round((d-1)/2) if self.planeCombo.currentIndex == 1 else (d-1)/2 for d in dimensions]
        center = list(matrix.MultiplyPoint(ijk + [1])[:3])
        transform = self.currentVolume.GetParentTransformNode()
        if transform:
            toWorld = vtk.vtkGeneralTransform()
            transform.GetTransformToWorld(toWorld)
            center = list(toWorld.TransformPoint(center))
        self.crosshairNode().SetCrosshairRAS(center)
        slicer.vtkMRMLSliceNode.JumpAllSlices(slicer.mrmlScene, *center, slicer.vtkMRMLSliceNode.CenteredJumpSlice)
        # Fit each pane independently at its actual aspect ratio, without linked zoom propagation.
        composites = [manager.sliceWidget(n).sliceLogic().GetSliceCompositeNode() for n in ("Red", "Yellow", "Green")]
        linked = [n.GetLinkedControl() for n in composites]
        try:
            for node in composites:
                node.SetLinkedControl(False)
            slicer.app.processEvents()
            for name in ("Red", "Yellow", "Green"):
                pane = manager.sliceWidget(name)
                pane.sliceLogic().FitSliceToVolume(self.currentVolume, pane.sliceView().width, pane.sliceView().height)
                node = pane.mrmlSliceNode()
                fov = node.GetFieldOfView()
                node.SetFieldOfView(fov[0]*1.05, fov[1]*1.05, fov[2])
                node.UpdateMatrices()
            if self.commonScaleCheck.checked:
                panes = [manager.sliceWidget(n) for n in ("Red", "Yellow", "Green")]
                scale = max(p.mrmlSliceNode().GetFieldOfView()[axis] / max(1, (p.sliceView().width, p.sliceView().height)[axis])
                            for p in panes for axis in (0, 1))
                for pane in panes:
                    node = pane.mrmlSliceNode()
                    node.SetFieldOfView(scale*pane.sliceView().width, scale*pane.sliceView().height, node.GetFieldOfView()[2])
                    node.UpdateMatrices()
        finally:
            for node, value in zip(composites, linked):
                node.SetLinkedControl(value)
        if self._initialCamera:
            camera = slicer.modules.cameras.logic().GetViewActiveCameraNode(manager.threeDWidget(0).mrmlViewNode())
            camera.GetCamera().DeepCopy(self._initialCamera)
            camera.Modified()
        cameraNode = slicer.modules.cameras.logic().GetViewActiveCameraNode(manager.threeDWidget(0).mrmlViewNode())
        cameraNode.GetCamera().SetParallelProjection(True)
        slicer.util.resetThreeDViews()
        cameraNode.GetCamera().Zoom(1.4)
        cameraNode.Modified()
        manager.threeDWidget(0).mrmlViewNode().SetBoxVisible(False)
        self.onCrosshairToggled(self.crosshairCheck.checked)
        self.onSlicePlanesToggled(self.slicePlanesCheck.checked)
        if self._viewLabels is None:
            self._viewLabels = ReviewLabels(self)
        self._viewLabels.update()
        self.statusLabel.text = "已恢复四视图方向与缩放；显示对比度和裁剪范围已保留。"

    def onPlaneModeChanged(self, *_):
        if not self.nodeIsPresent(self.currentVolume):
            return
        transform = self.currentVolume.GetParentTransformNode()
        if self.planeCombo.currentIndex == 1 and transform and not transform.IsTransformToWorldLinear():
            self.planeCombo.blockSignals(True)
            self.planeCombo.setCurrentIndex(0)
            self.planeCombo.blockSignals(False)
            self.showError("该序列含非线性采集几何，不能用单一平面声称显示原始采集面；保留标准 MPR。")
        self.onResetReviewViews()

    def applyDisplayInterpolation(self, *_):
        if not self.nodeIsPresent(self.currentVolume):
            return
        smooth = bool(self.interpolateCheck.checked)
        self.currentVolume.GetDisplayNode().SetInterpolate(smooth)
        if self.nodeIsPresent(self.currentVRDisplay):
            prop = self.currentVRDisplay.GetVolumePropertyNode().GetVolumeProperty()
            prop.SetInterpolationTypeToLinear()

    def setupRenderingPresets(self):
        self.renderPresetCombo.blockSignals(True)
        self.renderPresetCombo.clear()
        self.renderPresetCombo.addItem("跟随二维对比度", "")
        if self.loadedQuality["modality"] == "CT" and self.loadedQuality["huEligible"]:
            choices = [("CT 软组织", "CT-Soft-Tissue"), ("CT 肺", "CT-Lung"), ("CT 骨", "CT-Bone")]
        elif self.loadedQuality["modality"] == "MR":
            choices = [("MR 通用（需按序列调整）", "MR-Default"), ("MR 最大强度投影", "MR-MIP")]
        else:
            choices = []
        for label, preset in choices:
            if slicer.modules.volumerendering.logic().GetPresetByName(preset):
                self.renderPresetCombo.addItem(label, preset)
        selected = self.currentVRDisplay.GetAttribute("CT3D.Preset") or ""
        index = self.renderPresetCombo.findData(selected)
        self.renderPresetCombo.setCurrentIndex(max(0, index))
        self.renderPresetCombo.blockSignals(False)

    def onRenderPresetChanged(self, index):
        if not self.nodeIsPresent(self.currentVRDisplay) or index < 0:
            return
        presetName = str(self.renderPresetCombo.itemData(index) or "")
        logic = slicer.modules.volumerendering.logic()
        if presetName:
            preset = logic.GetPresetByName(presetName)
            if not preset:
                self.showError("本机未安装这个体渲染预设，请使用跟随二维对比度。")
                return
            self.currentVRDisplay.SetFollowVolumeDisplayNode(False)
            self.currentVRDisplay.GetVolumePropertyNode().Copy(preset)
        else:
            self.currentVRDisplay.SetFollowVolumeDisplayNode(True)
            logic.CopyDisplayToVolumeRenderingDisplayNode(self.currentVRDisplay, self.currentVolume.GetDisplayNode())
        self.currentVRDisplay.SetAttribute("CT3D.Preset", presetName)
        viewNode = slicer.app.layoutManager().threeDWidget(0).mrmlViewNode()
        viewNode.SetRaycastTechnique(viewNode.MaximumIntensityProjection if presetName.endswith("-MIP") else viewNode.Composite)
        self.applyDisplayInterpolation()

    def roiNode(self):
        if not self.nodeIsPresent(self.currentVRDisplay):
            return None
        roi = self.currentVRDisplay.GetMarkupsROINode()
        if not roi:
            logic = slicer.modules.volumerendering.logic()
            logic.CreateROINode(self.currentVRDisplay)
            logic.FitROIToVolume(self.currentVRDisplay)
            roi = self.currentVRDisplay.GetMarkupsROINode()
        if not roi:
            raise RuntimeError("本机无法建立可编辑的裁剪框。")
        roi.CreateDefaultDisplayNodes()
        roi.GetDisplayNode().SetHandlesInteractive(True)
        return roi

    def syncRenderingControls(self):
        if not self.nodeIsPresent(self.currentVRDisplay):
            return
        roi = self.currentVRDisplay.GetMarkupsROINode()
        for control, value in (
            (self.renderCheck, self.currentVRDisplay.GetVisibility()),
            (self.cropCheck, self.currentVRDisplay.GetCroppingEnabled()),
            (self.roiVisibleCheck, roi and roi.GetDisplayNode() and roi.GetDisplayNode().GetVisibility()),
        ):
            control.blockSignals(True)
            control.checked = bool(value)
            control.blockSignals(False)

    def onCropToggled(self, enabled):
        if not self.nodeIsPresent(self.currentVRDisplay):
            return
        try:
            roi = self.roiNode()
            self.currentVRDisplay.SetCroppingEnabled(bool(enabled))
            roi.GetDisplayNode().SetVisibility(bool(enabled))
            self.syncRenderingControls()
        except Exception as exc:
            self.syncRenderingControls()
            self.showError("无法启用显示裁剪：\n" + str(exc))

    def onROIVisibilityToggled(self, enabled):
        try:
            roi = self.roiNode()
            if roi:
                roi.GetDisplayNode().SetVisibility(bool(enabled))
        except Exception as exc:
            self.syncRenderingControls()
            self.showError("无法显示裁剪框：\n" + str(exc))

    def onFitROI(self):
        if not self.nodeIsPresent(self.currentVRDisplay):
            return
        try:
            self.roiNode()
            slicer.modules.volumerendering.logic().FitROIToVolume(self.currentVRDisplay)
            self.statusLabel.text = "裁剪框已恢复为完整体数据范围。"
        except Exception as exc:
            self.showError("无法恢复裁剪范围：\n" + str(exc))

    def onCenterROI(self):
        try:
            roi = self.roiNode()
            if roi:
                roi.SetCenterWorld(self.crosshairNode().GetCrosshairRAS())
                roi.GetDisplayNode().SetVisibility(True)
                self.syncRenderingControls()
        except Exception as exc:
            self.showError("无法将裁剪框移至十字线：\n" + str(exc))
