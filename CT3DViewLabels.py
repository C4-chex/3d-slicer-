"""Fixed-purpose anatomical labels; no DICOM names or free-text annotations."""
import slicer
import vtk

SAFE_ACTOR_ADDRESSES = set()


def isReviewLabel(actor):
    return actor.GetAddressAsString("") in SAFE_ACTOR_ADDRESSES


def direction(vector):
    positive, negative = "RAS", "LPI"
    axes = sorted(range(3), key=lambda i: abs(vector[i]), reverse=True)
    return "".join((positive[i] if vector[i] >= 0 else negative[i])
                   for i in axes if abs(vector[i]) > 0.2)


class ReviewLabels:
    def __init__(self, widget):
        self.widget = widget
        self.actors = []
        self.observers = []
        self.slices = {}
        manager = slicer.app.layoutManager()
        for name in ("Red", "Yellow", "Green"):
            view = manager.sliceWidget(name).sliceView()
            renderer = view.renderWindow().GetRenderers().GetFirstRenderer()
            node = manager.sliceWidget(name).mrmlSliceNode()
            labels = [self.add(renderer, x, y, align) for x, y, align in
                      ((.025, .97, "left"), (.025, .5, "left"), (.975, .5, "right"),
                       (.5, .97, "center"), (.5, .035, "center"))]
            self.slices[name] = (node, labels)
            self.observers.append((node, node.AddObserver(vtk.vtkCommand.ModifiedEvent, self.update)))
        renderer = manager.threeDWidget(0).threeDView().renderWindow().GetRenderers().GetFirstRenderer()
        self.volumeLabel = self.add(renderer, .025, .97, "left")
        self.update()

    def add(self, renderer, x, y, align):
        actor = vtk.vtkTextActor()
        actor.GetPositionCoordinate().SetCoordinateSystemToNormalizedViewport()
        actor.SetPosition(x, y)
        prop = actor.GetTextProperty()
        prop.SetFontSize(17)
        prop.SetColor(1, 1, 1)
        prop.SetShadow(True)
        prop.SetVerticalJustificationToTop() if y > .9 else prop.SetVerticalJustificationToCentered()
        if align == "right":
            prop.SetJustificationToRight()
        elif align == "center":
            prop.SetJustificationToCentered()
        else:
            prop.SetJustificationToLeft()
        renderer.AddViewProp(actor)
        self.actors.append((renderer, actor))
        SAFE_ACTOR_ADDRESSES.add(actor.GetAddressAsString(""))
        return actor

    def update(self, *_):
        if slicer.mrmlScene.IsClosing():
            return
        mode = "Acq. aligned" if self.widget.planeCombo.currentIndex == 1 else "MPR"
        for node, labels in self.slices.values():
            matrix = node.GetSliceToRAS()
            right = [matrix.GetElement(i, 0) for i in range(3)]
            up = [matrix.GetElement(i, 1) for i in range(3)]
            normal = [matrix.GetElement(i, 2) for i in range(3)]
            title = ("Sagittal", "Coronal", "Axial")[max(range(3), key=lambda i: abs(normal[i]))]
            labels[0].SetInput(title + " | " + mode)
            for actor, vector in zip(labels[1:], ([-v for v in right], right, up, [-v for v in up])):
                actor.SetInput(direction(vector))
        spacing = self.widget.currentVolume.GetSpacing() if self.widget.currentVolume else None
        text = "3D volume | display only"
        if spacing:
            text += "\nSource voxels: " + " x ".join(f"{v:.2f}" for v in spacing) + " mm"
        self.volumeLabel.SetInput(text)

    def cleanup(self):
        for node, observer in self.observers:
            node.RemoveObserver(observer)
        for renderer, actor in self.actors:
            renderer.RemoveViewProp(actor)
            SAFE_ACTOR_ADDRESSES.discard(actor.GetAddressAsString(""))
        self.observers.clear()
        self.actors.clear()
