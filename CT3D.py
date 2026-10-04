# -*- coding: utf-8 -*-
"""CT3D: local DICOM review module for 3D Slicer."""

import math
import json
import os
import sys
import uuid
from datetime import datetime

import ctk
import qt
import slicer
import vtk
from DICOMLib import DICOMUtils
from slicer.ScriptedLoadableModule import ScriptedLoadableModule, ScriptedLoadableModuleWidget
from CT3DLib.CT3DReview import ReviewControlsMixin
from CT3DLib.CT3DMeasurements import MeasurementControlsMixin
from CT3DLib.CT3DExport import captureReviewImage, reviewDisplayParameters

_ct_rules = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "ct part", "CT3DCTLib"))
if _ct_rules not in sys.path:
    sys.path.insert(0, _ct_rules)
from CT3DCTQuality import qualifies_hounsfield_units


class CT3D(ScriptedLoadableModule):
    def __init__(self, parent):
        super().__init__(parent)
        self.parent.title = "CT3D"
        self.parent.categories = ["CT3D"]
        self.parent.dependencies = ["DICOM", "VolumeRendering"]
        self.parent.contributors = ["CT3D"]
        self.parent.helpText = "本地 DICOM 序列核对、三视图和三维体渲染。研究用途。"
        self.parent.acknowledgementText = "依托 3D Slicer、ITK/GDCM 与 VTK。"


class CT3DWidget(ScriptedLoadableModuleWidget, ReviewControlsMixin, MeasurementControlsMixin):
    def setup(self):
        super().setup()
        self.series = []
        self.currentVolume = None
        self.currentVRDisplay = None
        self.currentQuality = None
        self.loadedQuality = None
        self.currentSegmentation = None
        self.currentSegmentID = None
        self.importFolder = None
        self._sessions = {}
        self._loading = False

        root = self.parent.layout()
        if root is None:
            root = qt.QVBoxLayout(self.parent)
        title = qt.QLabel("CT3D · 本地影像浏览")
        title.setStyleSheet("font-size:18px;font-weight:600;padding:4px")
        root.addWidget(title)

        intro = qt.QLabel("按 DICOM 患者坐标读取单一序列。源文件只读；不会把 MR 强度标作 HU。")
        intro.setWordWrap(True)
        root.addWidget(intro)

        self.importButton = qt.QPushButton("导入 DICOM 文件夹…")
        self.importButton.clicked.connect(self.onImportFolder)
        root.addWidget(self.importButton)

        root.addWidget(qt.QLabel("待载入序列 · 先选序列，再点击载入"))
        self.seriesCombo = qt.QComboBox()
        self.seriesCombo.currentIndexChanged.connect(self.onSeriesChanged)
        root.addWidget(self.seriesCombo)

        qualityBox = ctk.ctkCollapsibleButton()
        qualityBox.text = "数据质量与输出"
        qualityBox.collapsed = True
        qualityLayout = qt.QVBoxLayout(qualityBox)
        root.addWidget(qualityBox)
        self.qualityBox = qualityBox
        self.qualityText = qt.QTextEdit()
        self.qualityText.setReadOnly(True)
        self.qualityText.setMinimumHeight(180)
        self.qualityText.setPlainText("选择 DICOM 文件夹后，查看序列几何和像素变换检查结果。")
        qualityLayout.addWidget(self.qualityText)

        self.exportReportButton = qt.QPushButton("导出序列质量报告…")
        self.exportReportButton.enabled = False
        self.exportReportButton.clicked.connect(self.onExportQualityReport)
        qualityLayout.addWidget(self.exportReportButton)

        self.exportViewButton = qt.QPushButton("导出四视图截图与参数…")
        self.exportViewButton.enabled = False
        self.exportViewButton.clicked.connect(self.onExportViewSnapshot)
        qualityLayout.addWidget(self.exportViewButton)

        self.loadButton = qt.QPushButton("载入选中序列")
        self.loadButton.enabled = False
        self.loadButton.clicked.connect(self.onLoadSeries)
        root.addWidget(self.loadButton)

        self.setupReviewControls(root)
        self.setupMeasurementControls(root)

        segmentationBox = ctk.ctkCollapsibleButton()
        segmentationBox.text = "三维结构建模（手动分割）"
        segmentationBox.collapsed = True
        segmentationLayout = qt.QVBoxLayout(segmentationBox)
        segmentationHint = qt.QLabel(
            "先在切片上勾画结构，再由分割结果生成表面模型；不会自动推断器官。"
        )
        segmentationHint.setWordWrap(True)
        segmentationLayout.addWidget(segmentationHint)
        self.editSegmentationButton = qt.QPushButton("创建结构并开始分割")
        self.editSegmentationButton.enabled = False
        self.editSegmentationButton.clicked.connect(self.onStartSegmentation)
        segmentationLayout.addWidget(self.editSegmentationButton)
        self.updateSurfaceButton = qt.QPushButton("生成 / 更新三维表面")
        self.updateSurfaceButton.enabled = False
        self.updateSurfaceButton.clicked.connect(self.onUpdateSurface)
        segmentationLayout.addWidget(self.updateSurfaceButton)
        self.saveSegmentationButton = qt.QPushButton("保存可继续编辑的分割…")
        self.saveSegmentationButton.enabled = False
        self.saveSegmentationButton.clicked.connect(self.onSaveSegmentation)
        segmentationLayout.addWidget(self.saveSegmentationButton)
        self.exportMeshButton = qt.QPushButton("导出 STL 表面模型…")
        self.exportMeshButton.enabled = False
        self.exportMeshButton.clicked.connect(self.onExportMesh)
        segmentationLayout.addWidget(self.exportMeshButton)
        root.addWidget(segmentationBox)

        self.statusLabel = qt.QLabel("就绪。所有处理在本机完成。")
        self.statusLabel.setWordWrap(True)
        root.addWidget(self.statusLabel)
        root.addStretch(1)

        self.installReviewLayout()
        self._sceneCloseObserver = slicer.mrmlScene.AddObserver(
            slicer.vtkMRMLScene.EndCloseEvent, self.onSceneClosed)
        self._sceneRemovalObserver = slicer.mrmlScene.AddObserver(
            slicer.vtkMRMLScene.NodeRemovedEvent, self.onSceneNodeRemoved)

    def cleanup(self):
        self.cleanupMeasurements()
        if self._viewLabels:
            self._viewLabels.cleanup()
            self._viewLabels = None
        self.detachDisplayObserver()
        for observer in (self._sceneCloseObserver, self._sceneRemovalObserver):
            slicer.mrmlScene.RemoveObserver(observer)

    def enter(self):
        if hasattr(self, "loadedLabel"):
            self.updateReviewState()

    def onSceneClosed(self, *_):
        self.cleanupMeasurements()
        if self._viewLabels:
            self._viewLabels.cleanup()
            self._viewLabels = None
        self.detachDisplayObserver()
        self.currentVolume = self.currentVRDisplay = self.loadedQuality = None
        self.currentSegmentation = self.currentSegmentID = None
        self._initialCamera = None
        self._sessions.clear()
        self.updateReviewState()

    def onSceneNodeRemoved(self, *_):
        if self._loading or slicer.mrmlScene.IsClosing():
            return
        if self.currentVolume and not self.nodeIsPresent(self.currentVolume):
            self.detachDisplayObserver()
            self.currentVolume = self.currentVRDisplay = self.loadedQuality = None
            self.currentSegmentation = self.currentSegmentID = None
        if self.currentSegmentation and not self.nodeIsPresent(self.currentSegmentation):
            self.currentSegmentation = self.currentSegmentID = None
        self.updateReviewState()

    def installReviewLayout(self):
        """Four equal panes: axial/coronal above sagittal/3D."""
        layoutId = 501
        layoutNode = slicer.app.layoutManager().layoutLogic().GetLayoutNode()
        description = '''
        <layout type="grid">
              <item row="0" column="0"><view class="vtkMRMLSliceNode" singletontag="Red">
                <property name="orientation" action="default">Axial</property>
                <property name="viewlabel" action="default">A</property>
              </view></item>
                  <item row="0" column="1"><view class="vtkMRMLSliceNode" singletontag="Yellow">
                    <property name="orientation" action="default">Coronal</property>
                    <property name="viewlabel" action="default">C</property>
                  </view></item>
                  <item row="1" column="0"><view class="vtkMRMLSliceNode" singletontag="Green">
                    <property name="orientation" action="default">Sagittal</property>
                    <property name="viewlabel" action="default">S</property>
                  </view></item>
          <item row="1" column="1"><view class="vtkMRMLViewNode" singletontag="1" /></item>
        </layout>'''
        layoutNode.AddLayoutDescription(layoutId, description)
        slicer.app.layoutManager().setLayout(layoutId)

    def onImportFolder(self):
        folder = qt.QFileDialog.getExistingDirectory(self.parent, "选择 DICOM 文件夹")
        if not folder:
            return
        database = slicer.dicomDatabase
        if not database or not database.isOpen:
            databaseDir = os.path.join(os.path.dirname(__file__), "Data", "DICOM")
            os.makedirs(databaseDir, exist_ok=True)
            if not DICOMUtils.openDatabase(databaseDir) or not slicer.dicomDatabase.isOpen:
                self.showError("无法在本机打开 CT3D 的 DICOM 索引库。请检查 D:\\3d - 1\\CT3D\\Data 目录权限。")
                return
            database = slicer.dicomDatabase
        self.importButton.enabled = False
        self.statusLabel.setText("正在索引 DICOM 元数据；影像像素尚未解码…")
        slicer.app.processEvents()
        try:
            DICOMUtils.importDicom(folder, database)
            self.importFolder = os.path.realpath(folder)
            self.populateSeries()
            self.statusLabel.setText("扫描完成。请选择目标序列并先核对质量信息。")
        except Exception as exc:
            self.statusLabel.setText("扫描失败；原始 DICOM 未修改。")
            self.showError("DICOM 索引失败：\n" + str(exc))
        finally:
            self.importButton.enabled = True

    @staticmethod
    def tag(database, path, tagName):
        try:
            return str(database.fileValue(path, tagName) or "").strip()
        except Exception:
            return ""

    @staticmethod
    def numbers(value):
        try:
            return [float(x) for x in value.replace(",", "\\").split("\\") if x.strip()]
        except (TypeError, ValueError):
            return []

    def inspectSeries(self, uid, files):
        db = slicer.dicomDatabase
        first = files[0]
        modality = self.tag(db, first, "0008,0060").upper()
        description = self.tag(db, first, "0008,103E") or "未命名序列"
        rows = self.tag(db, first, "0028,0010")
        columns = self.tag(db, first, "0028,0011")
        spacing = self.numbers(self.tag(db, first, "0028,0030"))
        orientations, positions, slopes, intercepts = [], [], [], []
        rescaleTypes = set()
        imageTypes = set()
        multiEnergyValues = set()
        transformTagsComplete = True
        modalities = set()
        errors, warnings = [], []
        for path in files:
            modalities.add(self.tag(db, path, "0008,0060").upper())
            rowCount = self.tag(db, path, "0028,0010")
            colCount = self.tag(db, path, "0028,0011")
            if rowCount != rows or colCount != columns:
                errors.append("序列内图像矩阵尺寸不一致")
            orient = self.numbers(self.tag(db, path, "0020,0037"))
            position = self.numbers(self.tag(db, path, "0020,0032"))
            pixelSpacing = self.numbers(self.tag(db, path, "0028,0030"))
            if len(orient) != 6 or len(position) != 3 or len(pixelSpacing) != 2:
                errors.append("有切片缺少方向、位置或像素间距，无法核实几何")
                continue
            if not all(math.isfinite(value) for value in orient + position + pixelSpacing):
                errors.append("几何元数据含非有限数值")
                continue
            orientations.append(orient)
            positions.append(position)
            if len(spacing) == 2 and any(abs(a-b) > 1e-4 for a, b in zip(spacing, pixelSpacing)):
                errors.append("序列内像素间距不一致")
            slope = self.tag(db, path, "0028,1053")
            intercept = self.tag(db, path, "0028,1052")
            if modality == "CT" and (not slope or not intercept):
                transformTagsComplete = False
                errors.append("CT 切片缺少明确的 Rescale Slope/Intercept，不能确认 HU")
            rescaleTypes.add(self.tag(db, path, "0028,1054").upper())
            imageTypes.update(part.strip().upper() for part in self.tag(db, path, "0008,0008").split("\\") if part.strip())
            multiEnergyValues.add(self.tag(db, path, "0018,9361").upper())
            try:
                slopeValue, interceptValue = float(slope or "1"), float(intercept or "0")
                if not all(math.isfinite(v) for v in (slopeValue, interceptValue)) or slopeValue == 0:
                    raise ValueError()
                slopes.append(slopeValue)
                intercepts.append(interceptValue)
            except ValueError:
                errors.append("像素强度变换参数不是有效的有限数值")

        if orientations:
            reference = orientations[0]
            if any(max(abs(a-b) for a, b in zip(reference, item)) > 0.001 for item in orientations[1:]):
                errors.append("序列内图像方向不一致")
            r = reference[:3]
            c = reference[3:]
            rowNorm = math.sqrt(sum(value*value for value in r))
            colNorm = math.sqrt(sum(value*value for value in c))
            if (abs(rowNorm-1.0) > 0.01 or abs(colNorm-1.0) > 0.01
                    or abs(sum(a*b for a, b in zip(r, c))) > 0.01):
                errors.append("方向余弦无效或不正交，无法建立可靠患者坐标")
            normal = [r[1]*c[2]-r[2]*c[1], r[2]*c[0]-r[0]*c[2], r[0]*c[1]-r[1]*c[0]]
            projected = sorted(sum(p[i]*normal[i] for i in range(3)) for p in positions)
            deltas = [projected[i+1]-projected[i] for i in range(len(projected)-1)]
            if any(abs(delta) < 0.01 for delta in deltas):
                errors.append("发现重复的空间位置；可能混有回波、时间相位或重建序列，不能直接堆叠")
            positive = [d for d in deltas if d > 0.01]
            if len(positive) > 1:
                median = sorted(positive)[len(positive)//2]
                if max(positive)-min(positive) > max(0.1, median*0.05):
                    warnings.append("层距不规则；载入时保留 Slicer 的采集几何变换，不对源体数据重采样")
                thickness = self.numbers(self.tag(db, first, "0018,0050"))
                if thickness and median > thickness[0]*1.5:
                    warnings.append("切片间距明显大于层厚，可能存在缺片；不会自动插值")
            sliceSpacing = [min(positive), max(positive)] if positive else []
        else:
            errors.append("没有足够的空间信息验证序列几何")
            sliceSpacing = []

        if len(modalities) > 1:
            errors.append("序列内模态标记不一致")
        variantTags = {
            "0018,1210": "重建核",
            "0020,0100": "时间相",
            "0018,0086": "回波编号",
            "0020,0012": "采集编号",
            "0008,0008": "图像类型",
        }
        for tagName, label in variantTags.items():
            variants = {self.tag(db, path, tagName) for path in files}
            if len(variants) > 1:
                errors.append(f"序列内{label}不一致，不能直接堆叠")
        burnedInValues = {self.tag(db, path, "0028,0301").upper() for path in files}
        if "YES" in burnedInValues:
            burnedInAnnotation = "YES"
        elif burnedInValues == {"NO"}:
            burnedInAnnotation = "NO"
        else:
            burnedInAnnotation = "UNKNOWN"
        if len(spacing) == 2 and any(value <= 0 for value in spacing):
            errors.append("像素间距无效")

        huEligible, huReason = (False, "")
        if modality == "CT":
            unitEligible, huReason = qualifies_hounsfield_units(
                imageTypes, multiEnergyValues, rescaleTypes, slopes, intercepts,
                transformTagsComplete)
            huEligible = unitEligible and not errors
            if unitEligible and errors:
                huReason = "CT 像素值单位符合 HU 规则，但序列未通过完整性/几何检查"
            if "变换参数不一致" in huReason:
                errors.append(huReason)
        if modality == "MR":
            warnings.append("MR 强度不是 HU；CT 肺窗和骨窗预设不可用")
        elif modality == "CT" and not huEligible:
            warnings.append(huReason + "；允许浏览，禁用 CT 专用预设")
        if modality not in ("CT", "MR"):
            warnings.append("当前首版流程仅开放 CT 和 MR")

        return {
            "uid": uid, "files": files, "modality": modality, "description": description,
            "rows": rows, "columns": columns, "spacing": spacing,
            "sliceSpacing": sliceSpacing,
            "burnedInAnnotation": burnedInAnnotation,
            "imageTypes": sorted(imageTypes), "multiEnergyValues": sorted(multiEnergyValues),
            "huReason": huReason,
            "errors": list(dict.fromkeys(errors)), "warnings": list(dict.fromkeys(warnings)),
            "huEligible": huEligible,
        }

    def populateSeries(self):
        db = slicer.dicomDatabase
        candidates = []
        for patientUID in db.patients():
            for studyUID in db.studiesForPatient(patientUID):
                for seriesUID in db.seriesForStudy(studyUID):
                    files = list(db.filesForSeries(seriesUID))
                    if self.importFolder:
                        folderKey = os.path.normcase(self.importFolder)
                        files = [path for path in files if os.path.normcase(os.path.realpath(path)).startswith(folderKey + os.sep)]
                    if not files:
                        continue
                    info = self.inspectSeries(seriesUID, files)
                    if info["modality"] in ("CT", "MR"):
                        candidates.append(info)
        candidates.sort(key=lambda item: (item["modality"], item["description"].casefold(), len(item["files"])))
        self.series = candidates
        self.seriesCombo.blockSignals(True)
        self.seriesCombo.clear()
        for index, info in enumerate(candidates):
            info["displayCode"] = f"序列 {index+1:02d}"
            dimensions = f"{info['rows']}×{info['columns']}" if info["rows"] and info["columns"] else "矩阵未知"
            label = f"{info['displayCode']} · {info['modality']} · {info['description']} · {len(info['files'])} 张 · {dimensions}"
            if info["errors"]:
                label += " · 需处理"
            self.seriesCombo.addItem(label)
        self.seriesCombo.blockSignals(False)
        self.loadButton.enabled = False
        self.exportReportButton.enabled = False
        if candidates:
            self.seriesCombo.setCurrentIndex(0)
            self.onSeriesChanged(0)
        else:
            self.currentQuality = None
            self.qualityText.setPlainText("没有找到可用的 CT 或 MR 序列。")

    def onSeriesChanged(self, index):
        if index < 0 or index >= len(self.series):
            return
        self.currentQuality = self.series[index]
        info = self.currentQuality
        lines = [
            f"模态：{info['modality']}",
            f"切片数：{len(info['files'])}",
            f"矩阵：{info['rows']} × {info['columns']}",
            "像素间距：" + (" × ".join(f"{x:g}" for x in info["spacing"]) + " mm" if info["spacing"] else "未知"),
            "切片间距范围：" + (f"{info['sliceSpacing'][0]:.3f}–{info['sliceSpacing'][1]:.3f} mm" if info["sliceSpacing"] else "未知/单层"),
            "像素值：" + (("CT HU 已通过元数据规则核验：" + info["huReason"]) if info["huEligible"] else ("MR 强度（非 HU）" if info["modality"] == "MR" else "HU 未确认")),
            "采集几何规则化：载入时保留非线性变换，不硬化到像素数据。",
            "烧录注释标记：" + {
                "YES": "存在；截图导出已阻止",
                "NO": "标记为无；仍需检查图像内容",
                "UNKNOWN": "未声明或不一致；导出前需确认",
            }.get(info["burnedInAnnotation"], "未知"),
            "",
        ]
        lines.extend("错误：" + message for message in info["errors"])
        lines.extend("提示：" + message for message in info["warnings"])
        if not info["errors"] and not info["warnings"]:
            lines.append("序列几何检查通过。")
        self.qualityText.setPlainText("\n".join(lines))
        self.qualityBox.text = f"候选质量 · {len(info['errors'])} 项错误 / {len(info['warnings'])} 项提示"
        if info["errors"]:
            self.qualityBox.collapsed = False
        self.loadButton.enabled = not info["errors"]
        self.exportReportButton.enabled = True
        self.updateReviewState()

    def onExportQualityReport(self):
        """Export only non-identifying sequence geometry and QC findings."""
        info = self.currentQuality
        if not info:
            return
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        defaultPath = os.path.join(os.path.expanduser("~"), f"CT3D_质量报告_{stamp}.json")
        path = qt.QFileDialog.getSaveFileName(
            self.parent, "导出序列质量报告", defaultPath, "JSON 文件 (*.json)"
        )
        if not path:
            return
        report = {
            "schema_version": 1,
            "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "application": "CT3D",
            "scope": "DICOM 序列几何与强度元数据检查；不包含患者姓名、患者 ID、UID 或源文件路径。",
            "series": {
                "modality": info["modality"],
                "slice_count": len(info["files"]),
                "matrix_rows_columns": [int(info["rows"]), int(info["columns"])]
                    if info["rows"].isdigit() and info["columns"].isdigit() else None,
                "pixel_spacing_mm": info["spacing"] or None,
                "slice_spacing_range_mm": info["sliceSpacing"] or None,
                "ct_hu_metadata_eligible": bool(info["huEligible"]),
                "ct_pixel_pipeline_validated": False,
                "burned_in_annotation": info["burnedInAnnotation"],
            },
            "quality": {
                "load_blocking_errors": info["errors"],
                "warnings": info["warnings"],
            },
        }
        if not path.lower().endswith(".json"):
            path += ".json"
        try:
            with open(path, "w", encoding="utf-8") as reportFile:
                json.dump(report, reportFile, ensure_ascii=False, indent=2)
                reportFile.write("\n")
            self.statusLabel.setText("序列质量报告已导出；报告未包含患者姓名、ID 或源文件路径。")
        except Exception as exc:
            self.showError("报告导出失败：\n" + str(exc))

    def onExportViewSnapshot(self):
        """Save the view area and non-identifying display parameters as a pair."""
        if not self.currentVolume or not self.loadedQuality:
            return
        if self.loadedQuality["burnedInAnnotation"] == "YES":
            self.showError(
                "DICOM 标记该序列含有烧录注释。为避免泄露影像内的身份信息，CT3D 已阻止截图导出。"
            )
            return
        answer = qt.QMessageBox.question(
            self.parent, "检查截图内容",
            "导出的截图只包含影像四视图，不包含 CT3D 面板。导出前请检查影像边缘是否烧录了姓名、ID、检查号或其他身份信息。\n\n确认后继续？",
            qt.QMessageBox.Yes | qt.QMessageBox.No,
            qt.QMessageBox.No,
        )
        if answer != qt.QMessageBox.Yes:
            return

        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        defaultPath = os.path.join(os.path.expanduser("~"), f"CT3D_视图_{stamp}.png")
        imagePath = qt.QFileDialog.getSaveFileName(
            self.parent, "导出四视图截图与参数", defaultPath, "PNG 图片 (*.png)"
        )
        if not imagePath:
            return
        if not imagePath.lower().endswith(".png"):
            imagePath += ".png"
        parametersPath = os.path.splitext(imagePath)[0] + ".json"
        if os.path.exists(parametersPath):
            overwrite = qt.QMessageBox.question(
                self.parent, "覆盖视图参数",
                "同名 JSON 参数文件已存在。是否覆盖 PNG 截图和 JSON 参数文件？",
                qt.QMessageBox.Yes | qt.QMessageBox.No,
                qt.QMessageBox.No,
            )
            if overwrite != qt.QMessageBox.Yes:
                return

        exportToken = uuid.uuid4().hex
        imageTemp = imagePath + "." + exportToken + ".tmp.png"
        parametersTemp = parametersPath + "." + exportToken + ".tmp"
        try:
            parameters = {
                "schema_version": 2,
                "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                "application": "CT3D",
                "privacy": {
                    "identity_metadata_in_json": False,
                    "software_text_overlays_hidden": True,
                    "burned_in_annotation_status": self.loadedQuality["burnedInAnnotation"],
                    "manual_visual_review_confirmed": True,
                },
                "volume": {
                    "modality": self.loadedQuality["modality"],
                },
                "quality": {
                    "load_blocking_errors": self.loadedQuality["errors"],
                    "warnings": self.loadedQuality["warnings"],
                },
            }
            parameters["display"] = captureReviewImage(imageTemp, lambda: reviewDisplayParameters(self))
            with open(parametersTemp, "w", encoding="utf-8") as parametersFile:
                json.dump(parameters, parametersFile, ensure_ascii=False, indent=2)
                parametersFile.write("\n")
            self.commitExportPair(((imageTemp, imagePath), (parametersTemp, parametersPath)), exportToken)
            self.statusLabel.setText(
                "四视图截图和参数已导出。分享前请再次确认截图中没有身份信息。"
            )
        except Exception as exc:
            for temporaryPath in (imageTemp, parametersTemp):
                try:
                    if os.path.exists(temporaryPath):
                        os.remove(temporaryPath)
                except OSError:
                    pass
            self.showError("截图或参数导出失败：\n" + str(exc))

    @staticmethod
    def commitExportPair(pairs, token):
        """Restore existing outputs if either rename fails (not crash-atomic)."""
        backups, installed = [], []
        try:
            for _, destination in pairs:
                if os.path.exists(destination):
                    backup = destination + "." + token + ".bak"
                    os.replace(destination, backup)
                    backups.append((backup, destination))
            for temporary, destination in pairs:
                os.replace(temporary, destination)
                installed.append(destination)
        except OSError:
            for destination in installed:
                os.remove(destination)
            for backup, destination in reversed(backups):
                os.replace(backup, destination)
            raise
        else:
            for backup, _ in backups:
                try:
                    os.remove(backup)
                except OSError:
                    pass  # Both final files succeeded; retain an undeletable backup.

    def onLoadSeries(self):
        info = self.currentQuality
        if not info or info["errors"] or self._loading:
            return
        self.stopMeasurementPlacement()
        if (self.nodeIsPresent(self.currentVolume) and self.loadedQuality
                and self.loadedQuality["uid"] == info["uid"]
                and set(self.loadedQuality["files"]) == set(info["files"])):
            self.onResetReviewViews()
            return
        oldSession = self.currentSession()
        if oldSession:
            self._sessions[oldSession["quality"]["uid"]] = oldSession
        beforeIDs = {slicer.mrmlScene.GetNthNode(i).GetID()
                     for i in range(slicer.mrmlScene.GetNumberOfNodes())}
        self._loading = True
        self.loadButton.enabled = False
        self.importButton.enabled = False
        self.seriesCombo.enabled = False
        self.statusLabel.setText("正在由 3D Slicer / ITK-GDCM 解码并载入当前序列…")
        slicer.app.processEvents()
        settings = qt.QSettings()
        regularizationKey = "DICOM/ScalarVolume/AcquisitionGeometryRegularization"
        previousRegularization = settings.value(regularizationKey, "default")
        try:
            # Keep any required acquisition-geometry correction as a transform.
            # Do not harden it, which would interpolate and rewrite voxel samples.
            settings.setValue(regularizationKey, "transform")
            saved = self._sessions.get(info["uid"])
            reuse = (saved and self.nodeIsPresent(saved["volume"])
                     and set(saved["quality"]["files"]) == set(info["files"]))
            if reuse:
                self.restoreSession(saved)
            else:
                # Load exactly the files inspected above, not all files in a shared database.
                loadables, _ = DICOMUtils.getLoadablesFromFileLists(
                    [info["files"]], ["DICOMScalarVolumePlugin"])
                DICOMUtils.selectHighestConfidenceLoadables(loadables)
                selected = [item for items in loadables.values() for item in items if item.selected]
                if len(selected) != 1:
                    raise RuntimeError("当前文件集合没有唯一的标量体解释，请核对方向、回波和时间相。")
                nodes = DICOMUtils.loadLoadables(loadables)
                volumes = []
                for item in nodes or []:
                    node = slicer.mrmlScene.GetNodeByID(item) if isinstance(item, str) else item
                    if node and node.IsA("vtkMRMLScalarVolumeNode") and node.GetImageData():
                        volumes.append(node)
                if len(volumes) != 1:
                    raise RuntimeError(f"该序列载入后得到 {len(volumes)} 个标量体；当前只接受单一体数据。")
                self.currentVolume = volumes[0]
                self.loadedQuality = info
                self.currentVolume.SetName(f"CT3D {info['modality']} - {len(info['files'])} slices")
                self.currentVRDisplay = None
                self.currentSegmentation = self.currentSegmentID = None
            self.detachDisplayObserver()
            if oldSession:
                self.setSessionVisibility(oldSession, False)
            self.showVolume(self.currentVolume, initialize=not reuse)
            if self.currentSegmentation and self.nodeIsPresent(self.currentSegmentation):
                self.currentSegmentation.GetDisplayNode().SetVisibility(True)
            self.updateReviewState()
            dims = self.currentVolume.GetImageData().GetDimensions()
            spacing = self.currentVolume.GetSpacing()
            transform = self.currentVolume.GetParentTransformNode()
            geometryNote = "含采集几何变换；三维显示可能在内部重采样，源像素保留。" if transform else "采集几何变换：未附加。"
            self.statusLabel.setText(
                f"已载入。体素矩阵 IJK：{dims[0]}×{dims[1]}×{dims[2]}；"
                f"间距：{spacing[0]:.4g}×{spacing[1]:.4g}×{spacing[2]:.4g} mm。{geometryNote}"
                + ("三维渲染暂不可用，二维可继续浏览。原因：" + self._renderIssue if not self.currentVRDisplay else "")
            )
            self._sessions[info["uid"]] = self.currentSession()
        except Exception as exc:
            self.detachDisplayObserver()
            newNodes = [slicer.mrmlScene.GetNthNode(i) for i in range(slicer.mrmlScene.GetNumberOfNodes())
                        if slicer.mrmlScene.GetNthNode(i).GetID() not in beforeIDs]
            for node in reversed(newNodes):
                if not node.GetSingletonTag():
                    slicer.mrmlScene.RemoveNode(node)
            self.restoreSession(oldSession)
            if self.nodeIsPresent(self.currentVolume):
                self.showVolume(self.currentVolume, initialize=False)
                self.setSessionVisibility(oldSession, True)
            else:
                slicer.util.setSliceViewerLayers(background=None, foreground=None, label=None)
            self.updateReviewState()
            self.statusLabel.setText("载入失败；已撤回本次新建节点，原始 DICOM 未修改。")
            self.showError("序列载入失败：\n" + str(exc))
        finally:
            settings.setValue(regularizationKey, previousRegularization)
            self._loading = False
            self.importButton.enabled = True
            self.seriesCombo.enabled = True
            self.loadButton.enabled = bool(self.currentQuality and not self.currentQuality["errors"])

    def currentSession(self):
        if not self.nodeIsPresent(self.currentVolume):
            return None
        return {"volume": self.currentVolume, "quality": self.loadedQuality,
                "vr": self.currentVRDisplay, "segmentation": self.currentSegmentation,
                "segmentID": self.currentSegmentID, "camera": self._initialCamera}

    def restoreSession(self, session):
        session = session or {}
        self.currentVolume = session.get("volume")
        self.loadedQuality = session.get("quality")
        self.currentVRDisplay = session.get("vr")
        self.currentSegmentation = session.get("segmentation")
        self.currentSegmentID = session.get("segmentID")
        self._initialCamera = session.get("camera")
        if not self.nodeIsPresent(self.currentSegmentation):
            self.currentSegmentation = self.currentSegmentID = None

    def setSessionVisibility(self, session, visible):
        vr = session.get("vr")
        if self.nodeIsPresent(vr):
            vr.SetVisibility(bool(visible and self.renderCheck.checked))
            if not visible and vr.GetMarkupsROINode():
                vr.GetMarkupsROINode().GetDisplayNode().SetVisibility(False)
        segmentation = session.get("segmentation")
        if self.nodeIsPresent(segmentation):
            segmentation.GetDisplayNode().SetVisibility(bool(visible))

    def onStartSegmentation(self):
        if not self.nodeIsPresent(self.currentVolume):
            return
        try:
            segmentationNode = self.currentSegmentation
            if not segmentationNode:
                segmentationNode = slicer.mrmlScene.AddNewNodeByClass(
                    "vtkMRMLSegmentationNode", "CT3D 手动分割"
                )
                segmentationNode.CreateDefaultDisplayNodes()
                segmentationNode.SetReferenceImageGeometryParameterFromVolumeNode(self.currentVolume)
                volumeTransform = self.currentVolume.GetTransformNodeID()
                if volumeTransform:
                    segmentationNode.SetAndObserveTransformNodeID(volumeTransform)
                self.currentSegmentation = segmentationNode
                self.currentSegmentID = segmentationNode.GetSegmentation().AddEmptySegment("Structure_1")
                segment = segmentationNode.GetSegmentation().GetSegment(self.currentSegmentID)
                segment.SetName("结构 1")
                segment.SetColor(0.95, 0.72, 0.22)
            elif not self.currentSegmentID or not segmentationNode.GetSegmentation().GetSegment(self.currentSegmentID):
                segmentation = segmentationNode.GetSegmentation()
                if segmentation.GetNumberOfSegments() > 0:
                    self.currentSegmentID = segmentation.GetNthSegmentID(0)

            slicer.util.selectModule("SegmentEditor")
            editor = slicer.modules.segmenteditor.widgetRepresentation().self().editor
            editorNode = editor.mrmlSegmentEditorNode()
            editorNode.SetAndObserveSegmentationNode(segmentationNode)
            editorNode.SetAndObserveSourceVolumeNode(self.currentVolume)
            editorNode.SetSelectedSegmentID(self.currentSegmentID)
            self.updateSurfaceButton.enabled = True
            self.saveSegmentationButton.enabled = True
            self.exportMeshButton.enabled = bool(self.nonEmptySegmentIDs())
            self._sessions[self.loadedQuality["uid"]] = self.currentSession()
            qt.QMessageBox.information(
                slicer.util.mainWindow(), "手动分割",
                "请在轴位、冠状位或矢状位切片上勾画结构。\n"
                "完成后，从顶部模块列表返回 CT3D，点击“生成 / 更新三维表面”。\n"
                "导出的 STL 仅是当前分割边界的网格，不代表自动识别或医学确认。",
            )
        except Exception as exc:
            self.showError("无法启动分割编辑：\n" + str(exc))

    def nonEmptySegmentIDs(self):
        if not self.currentSegmentation:
            return []
        ids = []
        segmentation = self.currentSegmentation.GetSegmentation()
        for index in range(segmentation.GetNumberOfSegments()):
            segmentID = segmentation.GetNthSegmentID(index)
            polyData = vtk.vtkPolyData()
            if (self.currentSegmentation.GetClosedSurfaceRepresentation(segmentID, polyData)
                    and polyData.GetNumberOfPoints() > 0):
                ids.append(segmentID)
        return ids

    def onUpdateSurface(self):
        if not self.currentSegmentation:
            return
        try:
            if not self.currentSegmentation.CreateClosedSurfaceRepresentation():
                raise RuntimeError("Slicer 无法从当前分割生成闭合表面。")
            displayNode = self.currentSegmentation.GetDisplayNode()
            displayNode.SetVisibility3D(True)
            displayNode.SetOpacity3D(0.65)
            slicer.app.layoutManager().setLayout(501)
            slicer.util.resetThreeDViews()
            segmentIDs = self.nonEmptySegmentIDs()
            self.exportMeshButton.enabled = bool(segmentIDs)
            if not segmentIDs:
                self.statusLabel.setText("已更新表面表示，但当前分割为空；请先在切片上勾画。")
                return
            self.statusLabel.setText(
                f"已从 {len(segmentIDs)} 个非空手动分割生成三维表面；可保存分割或导出 STL。"
            )
        except Exception as exc:
            self.exportMeshButton.enabled = False
            self.showError("三维表面生成失败：\n" + str(exc))

    def onSaveSegmentation(self):
        if not self.currentSegmentation:
            return
        if self.currentSegmentation.GetParentTransformNode():
            self.showError(
                "这个分割带有空间变换，单独保存 .seg.nrrd 会丢失世界坐标映射。\n"
                "请使用 Slicer 顶部 Save，将整个场景保存为 .mrb 包，保留影像、分割和变换。\n"
                "场景包可能包含患者信息，适合本机继续工作；分享前需另行去标识化。"
            )
            return
        defaultPath = os.path.join(os.path.expanduser("~"), "CT3D_手动分割.seg.nrrd")
        path = qt.QFileDialog.getSaveFileName(
            self.parent, "保存可继续编辑的分割", defaultPath, "Slicer 分割文件 (*.seg.nrrd)"
        )
        if not path:
            return
        if not path.lower().endswith(".seg.nrrd"):
            path += ".seg.nrrd"
        try:
            if not slicer.util.saveNode(self.currentSegmentation, path):
                raise RuntimeError("Slicer 未能保存分割文件。")
            self.statusLabel.setText("可继续编辑的分割已保存为 .seg.nrrd。")
        except Exception as exc:
            self.showError("分割保存失败：\n" + str(exc))

    def onExportMesh(self):
        if not self.currentSegmentation:
            return
        folder = qt.QFileDialog.getExistingDirectory(self.parent, "选择 STL 模型保存文件夹")
        if not folder:
            return
        try:
            if not self.currentSegmentation.CreateClosedSurfaceRepresentation():
                raise RuntimeError("Slicer 无法从当前分割生成闭合表面。")
            segmentIDs = vtk.vtkStringArray()
            for segmentID in self.nonEmptySegmentIDs():
                segmentIDs.InsertNextValue(segmentID)
            if segmentIDs.GetNumberOfValues() == 0:
                raise RuntimeError("当前分割为空；请先在切片上勾画结构。")
            success = slicer.vtkSlicerSegmentationsModuleLogic.ExportSegmentsClosedSurfaceRepresentationToFiles(
                folder, self.currentSegmentation, segmentIDs, "STL", True, 1.0, False
            )
            if not success:
                raise RuntimeError("Slicer 的 STL 导出器返回失败。")
            self.statusLabel.setText(
                f"已导出 {segmentIDs.GetNumberOfValues()} 个 STL 模型（LPS 毫米坐标）。"
            )
        except Exception as exc:
            self.showError("STL 导出失败：\n" + str(exc))

    def showVolume(self, volume, initialize=True):
        self._renderIssue = ""
        layoutManager = slicer.app.layoutManager()
        layoutManager.setLayout(501)
        for name, orientation in (("Red", "Axial"), ("Yellow", "Coronal"), ("Green", "Sagittal")):
            sliceWidget = layoutManager.sliceWidget(name)
            sliceWidget.sliceLogic().GetSliceNode().SetOrientation(orientation)
            sliceWidget.sliceLogic().GetSliceCompositeNode().SetLinkedControl(True)
        slicer.util.setSliceViewerLayers(background=volume, foreground=None, label=None)
        slicer.util.resetSliceViews()
        selectionNode = slicer.mrmlScene.GetNodeByID("vtkMRMLSelectionNodeSingleton")
        if selectionNode:
            selectionNode.SetReferenceActiveVolumeID(volume.GetID())
            slicer.app.applicationLogic().PropagateVolumeSelection()

        if initialize:
            if self.loadedQuality["modality"] == "CT" and self.loadedQuality["huEligible"]:
                volume.GetDisplayNode().SetAutoWindowLevel(False)
                volume.GetDisplayNode().SetWindowLevel(400, 40)
            else:
                volume.GetDisplayNode().SetAutoWindowLevel(True)
        try:
            vrLogic = slicer.modules.volumerendering.logic()
            if not self.nodeIsPresent(self.currentVRDisplay):
                self.currentVRDisplay = vrLogic.CreateDefaultVolumeRenderingNodes(volume)
            if not self.currentVRDisplay:
                raise RuntimeError("无法建立三维显示节点")
            self.currentVRDisplay.SetVisibility(bool(self.renderCheck.checked))
            self.setupRenderingPresets()
            if initialize:
                self.onRenderPresetChanged(0)
                roi = self.roiNode()
                roi.SetName("CT3D 裁剪范围")
                roi.GetDisplayNode().SetVisibility(False)
                roi.GetDisplayNode().SetPropertiesLabelVisibility(False)
                self.currentVRDisplay.SetCroppingEnabled(False)
        except Exception as exc:
            if self.nodeIsPresent(self.currentVRDisplay):
                self.currentVRDisplay.SetVisibility(False)
            self.currentVRDisplay = None
            self._renderIssue = str(exc)
        self.onCrosshairToggled(self.crosshairCheck.checked)
        self.onSlicePlanesToggled(self.slicePlanesCheck.checked)
        self.applyDisplayInterpolation()
        slicer.util.resetThreeDViews()
        if initialize or not self._initialCamera:
            self.rememberCamera()
        self.onResetReviewViews()

    def applyWindowLevel(self, width, level):
        if not self.nodeIsPresent(self.currentVolume) or not self.loadedQuality or not self.loadedQuality["huEligible"]:
            return
        displayNode = self.currentVolume.GetDisplayNode()
        displayNode.SetAutoWindowLevel(False)
        displayNode.SetWindowLevel(float(width), float(level))

    def applyAutoWindowLevel(self):
        if self.nodeIsPresent(self.currentVolume) and self.currentVolume.GetDisplayNode():
            self.currentVolume.GetDisplayNode().SetAutoWindowLevel(True)

    def onRenderToggled(self, state):
        if not self.nodeIsPresent(self.currentVolume):
            return
        if self.nodeIsPresent(self.currentVRDisplay):
            self.currentVRDisplay.SetVisibility(bool(state))

    def showError(self, message):
        qt.QMessageBox.critical(self.parent, "CT3D", message)


class CT3DLogic:
    pass
