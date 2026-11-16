"""Custom blocks that İSG Kamera registers in AugeLab Studio.

Studio generates each block's file from these definitions itself (node.custom.create). The blocks are thin: the real
work is in the isg_core package in the "isg_engine" folder placed next to Studio's custom block folder (the ONNX
models and onnxruntime are there too). No package is installed into Studio's own environment; the engine's extra
libraries are appended to the END of sys.path.

The saved scene (installer/scene/isg-kamera.pmod) binds the blocks by opCode and socket name: changing an opCode, a
title or a socket name (or the order of the sockets) needs a rebuilt scene (installer/build_scene.py). The generated
block code and its status texts are the same for every language (the block file must not change when the panel
language changes).
"""
from __future__ import annotations

ENGINE_FOLDER = "isg_engine"  # sibling of Studio's custom_blocks folder
VIDEO_OP = "IsgVideo"
VIDEO_TITLE = "ISG Video"
POSE_OP = "IsgPose"
POSE_TITLE = "ISG Pose"
DETECT_OP = "IsgDetect"
RULES_OP = "IsgRules"
DETECT_TITLE = "ISG Detection"
RULES_TITLE = "ISG Rules"
# opCodes of v0.1.0 whose block files (custom_blocks/<opCode>.py) are removed on upgrade
LEGACY_OP_CODES = ("IsgAlgila", "IsgIskelet", "IsgKurallar")

# Same in every block: find the engine, reload it if its version changed.
_LOADER = """\
folder = state.get('folder')
if folder is None:
    here = globals().get('__file__')
    if here:
        folder = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(here))), 'isg_engine')
    else:
        folder = os.path.join(os.environ.get('APPDATA', ''), 'AugeLab Studio', 'marketplace', 'isg_engine')
    if not os.path.isdir(os.path.join(folder, 'isg_core')):
        outputs['Status'] = 'error: engine not found (open the ISG Kamera panel once)'
        return
    for p in (folder, os.path.join(folder, 'deps')):
        if os.path.isdir(p) and p not in sys.path:
            sys.path.append(p)
    state['folder'] = folder
try:
    stamp = os.path.getmtime(os.path.join(folder, 'version.txt'))
except OSError:
    stamp = 0
if state.get('engine') is None or state.get('stamp') != stamp:
    signature = ''
    try:
        with open(os.path.join(folder, 'version.txt'), encoding='ascii') as f:
            signature = f.read()
    except OSError:
        pass
    old = sys.modules.get('isg_core')
    if old is not None and getattr(old, '_signature', None) != signature:
        for name in [m for m in sys.modules if m == 'isg_core' or m.startswith('isg_core.')]:
            del sys.modules[name]
    try:
        state['engine'] = importlib.import_module('isg_core.blocks')
        sys.modules['isg_core']._signature = signature
    except Exception as exc:
        state['engine'] = None
        outputs['Status'] = 'error: could not load the engine: %s: %s' % (type(exc).__name__, exc)
        return
    state['stamp'] = stamp
    state['state'] = {}
engine = state['engine']
"""

VIDEO_CODE = """\
outputs['Image'] = None
outputs['Time'] = 0
outputs['Done'] = False
outputs['Status'] = ''
""" + _LOADER + """\
try:
    image, time, done, status = engine.video(state['state'], inputs['Camera'] or '')
except Exception as exc:
    outputs['Status'] = 'error: %s: %s' % (type(exc).__name__, exc)
    return
outputs['Image'] = image
outputs['Time'] = time
outputs['Done'] = done
outputs['Status'] = status
"""

# The Detections output defaults to an "error" value (an empty list means "nobody there"): if the engine cannot be
# loaded or the block fails unexpectedly, ISG Rules sees it, evaluates no rules and saves no footage.
DETECT_CODE = """\
outputs['Detections'] = '{"error": "ISG Detection did not run"}'
outputs['Status'] = ''
""" + _LOADER + """\
try:
    detections, status = engine.detect(state['state'], inputs['Image'], inputs['Camera'] or '')
except Exception as exc:
    outputs['Detections'] = '{"error": "ISG Detection: %s"}' % type(exc).__name__
    outputs['Status'] = 'error: %s: %s' % (type(exc).__name__, exc)
    return
outputs['Detections'] = detections
outputs['Status'] = status
"""

POSE_CODE = """\
outputs['Detections'] = inputs['Detections'] or '[]'
outputs['Status'] = ''
""" + _LOADER + """\
try:
    detections, status = engine.pose(state['state'], inputs['Image'], inputs['Detections'] or '[]')
except Exception as exc:
    outputs['Status'] = 'error: %s: %s' % (type(exc).__name__, exc)
    return
outputs['Detections'] = detections
outputs['Status'] = status
"""

# The Image output defaults to empty: if the block fails, a raw frame with unmasked faces never reaches the output.
RULES_CODE = """\
outputs['Image'] = None
outputs['Alarm'] = False
outputs['Risk'] = 0
outputs['Events'] = '[]'
outputs['Status'] = ''
outputs['New Event'] = False
outputs['Write Alarm'] = False
""" + _LOADER + """\
try:
    image, alarm, risk, events, status, new_event, write_alarm = engine.rules(state['state'], inputs['Image'], inputs['Detections'] or '[]', inputs['Camera'] or '', inputs['Time'], bool(inputs['Done']))
except Exception as exc:
    outputs['Status'] = 'error: %s: %s' % (type(exc).__name__, exc)
    return
outputs['Image'] = image
outputs['Alarm'] = alarm
outputs['Risk'] = risk
outputs['Events'] = events
outputs['Status'] = status
outputs['New Event'] = new_event
outputs['Write Alarm'] = write_alarm
"""

DETECT = {
    "resolverKey": "isg_detect",
    "name": DETECT_OP,
    "opCode": DETECT_OP,
    "title": DETECT_TITLE,
    "kind": "computation",
    "description": (
        "Workplace safety detection: person, hard hat, vest, gloves, glasses, safety shoes (worn / not worn) and forklift. "
        "Runs the ONNX models from the marketplace/isg_engine folder; the settings of the camera code on the 'Camera' input "
        "decide which models are loaded. Output: JSON list of detections [{s: class, b: [x1, y1, x2, y2], p: confidence}] and status."
    ),
    "imports": ["sys", "os", "importlib"],
    "inputs": [{"name": "Image", "type": "ImageAny"}, {"name": "Camera", "type": "String"}],
    "outputs": [{"name": "Detections", "type": "String"}, {"name": "Status", "type": "String"}],
    "parameters": [],
    "processCode": DETECT_CODE,
}

RULES = {
    "resolverKey": "isg_rules",
    "name": RULES_OP,
    "opCode": RULES_OP,
    "title": RULES_TITLE,
    "kind": "computation",
    "description": (
        "Workplace safety rules: tracks the detections and applies the camera's zones and rules. Outputs: annotated image with "
        "faces masked; Alarm (True while a significant event is ongoing); Risk (0-25); started events (JSON); status; New Event "
        "(for MQTT Publish Enable); Write Alarm (True when the alarm changes and every 5 s; for Modbus / S7 Write Enable). "
        "Time and Done come from ISG Video."
    ),
    "imports": ["sys", "os", "importlib"],
    "inputs": [{"name": "Image", "type": "ImageAny"}, {"name": "Detections", "type": "String"}, {"name": "Camera", "type": "String"},
               {"name": "Time", "type": "Number"}, {"name": "Done", "type": "Boolean"}],
    "outputs": [{"name": "Image", "type": "ImageAny"}, {"name": "Alarm", "type": "Boolean"}, {"name": "Risk", "type": "Number"},
                {"name": "Events", "type": "String"}, {"name": "Status", "type": "String"}, {"name": "New Event", "type": "Boolean"},
                {"name": "Write Alarm", "type": "Boolean"}],
    "parameters": [],
    "processCode": RULES_CODE,
}

VIDEO = {
    "resolverKey": "isg_video",
    "name": VIDEO_OP,
    "opCode": VIDEO_OP,
    "title": VIDEO_TITLE,
    "kind": "computation",
    "description": (
        "Reads the video file in a camera's settings (e.g. footage exported from a recorder) frame by frame. The 'Camera' input "
        "is the camera code. Outputs: image, the image's time in seconds, Done (True once when the file ends) and status."
    ),
    "imports": ["sys", "os", "importlib"],
    "inputs": [{"name": "Camera", "type": "String"}],
    "outputs": [{"name": "Image", "type": "ImageAny"}, {"name": "Time", "type": "Number"}, {"name": "Done", "type": "Boolean"},
                {"name": "Status", "type": "String"}],
    "parameters": [],
    "processCode": VIDEO_CODE,
}

POSE = {
    "resolverKey": "isg_pose",
    "name": POSE_OP,
    "opCode": POSE_OP,
    "title": POSE_TITLE,
    "kind": "computation",
    "description": (
        "Pose (skeleton) estimation: enlarges the box of each person found by ISG Detection, finds 17 keypoints (nose, eyes, ears, "
        "shoulders, elbows, wrists, hips, knees, ankles) and adds them to the detections. Also works on small, distant people. "
        "ISG Rules uses these points for foot position, hand and foot protection, face masking, falls and the REBA posture score."
    ),
    "imports": ["sys", "os", "importlib"],
    "inputs": [{"name": "Image", "type": "ImageAny"}, {"name": "Detections", "type": "String"}],
    "outputs": [{"name": "Detections", "type": "String"}, {"name": "Status", "type": "String"}],
    "parameters": [],
    "processCode": POSE_CODE,
}

DEFINITIONS = [VIDEO, DETECT, POSE, RULES]


def signature() -> str:
    """Digest of the block definitions: if it changes, installed scenes are rebuilt with the new blocks."""
    import hashlib
    import json

    return hashlib.sha1(json.dumps(DEFINITIONS, sort_keys=True).encode("utf-8")).hexdigest()[:12]


def block_source(t: dict) -> str:
    """The block's file for Studio's custom block folder: identical to the file Studio's node.custom.create generates from
    the definition. In headless runs (studio run) Studio is not open, so the panel writes the file itself."""
    op = t["opCode"]
    inputs = ",\n".join(f"        SocketTypes.{g['type']}({g['name']!r}, multiple=False)" for g in t["inputs"])
    outputs = ",\n".join(f"        SocketTypes.{c['type']}({c['name']!r}, multiple=False)" for c in t["outputs"])
    names = ", ".join(repr(c["name"]) for c in t["outputs"])
    if len(t["outputs"]) == 1:
        names += ","
    process = "".join(("        " + s if s.strip() else "") + "\n" for s in t["processCode"].rstrip("\n").split("\n"))
    input_map = ",\n".join(f"            {g['name']!r}: self.input[{g['name']!r}].data" for g in t["inputs"])
    output_map = ",\n".join(f"            {c['name']!r}: None" for c in t["outputs"])
    imports = "".join(f"import {m}\n" for m in t.get("imports") or [])
    return (
        "from studio.custom_block import (\n"
        "    Block, CheckBox, DropDown, Label, SocketTypes, TextInput, add_block,\n"
        ")\n\n"
        f"{imports}\n"
        f"class {op}(Block):\n"
        f"    op_code = {op!r}\n"
        f"    input_sockets = [\n{inputs}\n    ]\n"
        f"    output_sockets = [\n{outputs}\n    ]\n"
        "    _augelab_generated_spec_version = 1\n"
        f"    _augelab_output_names = ({names})\n\n"
        "    def init(self):\n"
        f"        self.op_title = {t['title']!r}\n"
        "        self.width = 280\n"
        "        self.height = 150\n"
        f"        self.tooltip = {t['description']!r}\n"
        "        pass\n"
        "        self._augelab_generated_state = {}\n\n"
        "    @staticmethod\n"
        "    def _process(inputs, parameters, state, outputs):\n"
        f"{process}\n"
        "    def run(self):\n"
        f"        inputs = {{\n{input_map}\n        }}\n"
        "        parameters = {\n            \n        }\n"
        "        state = self._augelab_generated_state\n"
        f"        outputs = {{\n{output_map}\n        }}\n"
        "        self._process(inputs, parameters, state, outputs)\n"
        "        unknown = set(outputs).difference(self._augelab_output_names)\n"
        "        if unknown:\n"
        "            raise ValueError(f\"Undeclared generated outputs: {sorted(unknown)}\")\n"
        "        for output_name in self._augelab_output_names:\n"
        "            self.output[output_name].data = outputs[output_name]\n\n\n"
        f"add_block({op}.op_code, {op})\n"
    )
