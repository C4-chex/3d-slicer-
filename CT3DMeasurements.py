# -*- coding: utf-8 -*-
"""Source-bound, world-coordinate length measurements using Slicer Markups."""
import json
import math
import os
import uuid
from datetime import datetime

import ctk
import qt
import slicer


class MeasurementControlsMixin:
    def setupMeasurementControls(self, root):
        self._measurementObservers = []
        self.measurementBox = ctk.ctkCollapsibleButton()
        self.measurementBox.text = "长度测量（毫米）"
        self.measurementBox.collapsed = True
        layout = qt.QVBoxLayout(self.measurementBox)
        hint = qt.QLabel("在切片上点击两个端点；拖动端点可修正。结果是患者空间中的直线距离，不能提高原始影像精度。")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        self.addMeasurementButton = qt.QPushButton("新建两点长度测量")
        self.addMeasurementButton.clicked.connect(self.onAddMeasurement)
        layout.addWidget(self.addMeasurementButton)
        self.stopMeasurementButton = qt.QPushButton("结束放置")
        self.stopMeasurementButton.clicked.connect(self.stopMeasurementPlacement)
        layout.addWidget(self.stopMeasurementButton)
        self.measurementText = qt.QLabel("尚无测量。")
        self.measurementText.setWordWrap(True)
        layout.addWidget(self.measurementText)
        self.clearMeasurementsButton = qt.QPushButton("清除当前影像的测量")
        self.clearMeasurementsButton.clicked.connect(self.onClearMeasurements)
        layout.addWidget(self.clearMeasurementsButton)
        self.exportMeasurementsButton = qt.QPushButton("导出测量记录…")
        self.exportMeasurementsButton.clicked.connect(self.onExportMeasurements)
        layout.addWidget(self.exportMeasurementsButton)
        root.addWidget(self.measurementBox)
        self.updateMeasurementState()

    def measurementNodes(self, currentOnly=True):
        nodes = [n for n in slicer.util.getNodesByClass("vtkMRMLMarkupsLineNode")
                 if n.GetAttribute("CT3D.Measurement") == "length"]
        if currentOnly:
            nodes = [n for n in nodes if n.GetNodeReference("CT3D.SourceVolume") == self.currentVolume]
        return nodes

    @staticmethod
    def measurementRecord(node, index):
        if node.GetNumberOfControlPoints() != 2 or any(
                node.GetNthControlPointPositionStatus(i) != node.PositionDefined for i in range(2)):
            return None
        ras = [[0.0]*3 for _ in range(2)]
        for i in range(2):
            node.GetNthControlPointPositionWorld(i, ras[i])
        if not all(math.isfinite(v) for p in ras for v in p):
            return None
        return {"index": index, "length_mm": math.dist(*ras),
                "endpoints_world_lps_mm": [[-p[0], -p[1], p[2]] for p in ras]}

    def updateMeasurementState(self, *_):
        loaded = self.nodeIsPresent(self.currentVolume) and bool(self.loadedQuality)
        self.measurementBox.enabled = loaded
        lines = []
        completed = 0
        for index, node in enumerate(self.measurementNodes(), 1):
            record = self.measurementRecord(node, index)
            completed += record is not None
            lines.append(f"测量 {index}：{record['length_mm']:.2f} mm" if record else f"测量 {index}：等待两个有效端点")
        for node in self.measurementNodes(False):
            node.GetDisplayNode().SetVisibility(bool(loaded and node.GetNodeReference("CT3D.SourceVolume") == self.currentVolume))
        self.measurementText.text = "\n".join(lines) or "尚无测量。"
        self.exportMeasurementsButton.enabled = loaded and completed > 0
        self.clearMeasurementsButton.enabled = loaded and bool(lines)

    def stopMeasurementPlacement(self):
        interaction = slicer.app.applicationLogic().GetInteractionNode()
        interaction.SetPlaceModePersistence(0)
        interaction.SetCurrentInteractionMode(interaction.ViewTransform)

    def onAddMeasurement(self):
        if not self.nodeIsPresent(self.currentVolume) or self._loading:
            return
        self.stopMeasurementPlacement()
        node = slicer.mrmlScene.AddNewNodeByClass("vtkMRMLMarkupsLineNode", "CT3D 长度")
        node.CreateDefaultDisplayNodes()
        node.SetAttribute("CT3D.Measurement", "length")
        node.SetNodeReferenceID("CT3D.SourceVolume", self.currentVolume.GetID())
        node.SetAndObserveTransformNodeID(self.currentVolume.GetTransformNodeID())
        node.GetDisplayNode().SetSelectedColor(1.0, 0.8, 0.1)
        for event in (node.PointModifiedEvent, node.PointPositionDefinedEvent,
                      node.PointPositionUndefinedEvent, node.TransformModifiedEvent):
            self._measurementObservers.append((node, node.AddObserver(event, self.updateMeasurementState)))
        slicer.modules.markups.logic().SetActiveListID(node)
        # StartPlaceMode initializes the legacy fiducial class; set our type afterwards.
        slicer.modules.markups.logic().StartPlaceMode(True)
        selection = slicer.app.applicationLogic().GetSelectionNode()
        selection.SetReferenceActivePlaceNodeClassName("vtkMRMLMarkupsLineNode")
        selection.SetActivePlaceNodeID(node.GetID())
        self.updateMeasurementState()
        self.statusLabel.text = "在切片上依次点击两个端点；完成后点击“结束放置”或按 Esc。"
        return node

    def onClearMeasurements(self):
        self.stopMeasurementPlacement()
        nodes = self.measurementNodes()
        for node, observer in list(self._measurementObservers):
            if node in nodes:
                node.RemoveObserver(observer)
                self._measurementObservers.remove((node, observer))
        for node in nodes:
            slicer.mrmlScene.RemoveNode(node)
        self.updateMeasurementState()

    def cleanupMeasurements(self):
        for node, observer in self._measurementObservers:
            node.RemoveObserver(observer)
        self._measurementObservers.clear()

    def measurementPayload(self):
        records = [self.measurementRecord(node, i) for i, node in enumerate(self.measurementNodes(), 1)]
        records = [r for r in records if r]
        if not self.nodeIsPresent(self.currentVolume) or not records:
            raise ValueError("当前影像没有完成的两点测量。")
        return {"schema": "CT3D.length.v1", "created_at": datetime.now().isoformat(),
                "modality": self.loadedQuality["modality"], "coordinate_system": "world LPS", "units": "mm",
                "source_spacing_mm": list(self.currentVolume.GetSpacing()),
                "transform_present": bool(self.currentVolume.GetTransformNodeID()),
                "measurements": records,
                "note": "患者世界坐标中的直线距离；显示小数位不代表采集精度。记录不含患者身份或源路径，不能单独恢复完整场景。"}

    def saveMeasurements(self, path):
        payload = self.measurementPayload()
        temporary = path + "." + uuid.uuid4().hex + ".tmp"
        try:
            with open(temporary, "w", encoding="utf-8") as stream:
                json.dump(payload, stream, ensure_ascii=False, indent=2)
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.remove(temporary)

    def onExportMeasurements(self):
        path = qt.QFileDialog.getSaveFileName(self.parent, "导出测量记录", "lengths.json", "JSON (*.json)")
        if path:
            try:
                self.saveMeasurements(path)
                self.statusLabel.text = "已导出当前影像完成的测量；未包含身份信息、UID 或源文件路径。"
            except Exception as error:
                self.showError("测量记录导出失败：\n" + str(error))
