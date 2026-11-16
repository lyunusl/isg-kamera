"""Rule catalog: which hazard is detected and how.

Every rule has a "mechanism" (the evaluator in rule_engine.py):

  ppe          a person lacks required protective equipment       (class, evidence: negative | absence | attribute, zone_type?)
  posture      a state read from the pose: person lying down       (posture)
  ergonomics   posture score computed from the pose (REBA)         (threshold)
  object       object standing in a zone, prohibited or abandoned  (classes, zone_type?, unattended?)
  three_point  on a ladder, fewer than three of the hands and feet are on the ladder   (zone_type)
  zone         a person or vehicle entered a zone                  (subject, zone_type, hours?, authorized_color?)
  count        headcount in a zone above/below the limit           (max? | min?)
  proximity    two subjects too close to each other                (a, b, threshold: in person heights)
  speed        person running                                      (threshold: heights/s)
  off_lane     subject outside its own lane                        (subject, zone_type)
  wrong_way    subject moving against the zone's direction         (subject, zone_type)
  co_presence  a person and a vehicle in the same zone at the same time   (zone_type)
  none         not implemented

Fields:
  severity  1-5: how severe if it happens (5 = possible death or permanent disability)
  serious   True: event class with serious injury or fatality potential
  status    "ready": passed every test scene · "limited": works, but missed a case in testing (stated in the description)
            "weak": unreliable in testing, gives false alarms · "off": off (not implemented or no working model)
            The tests were run with AI-generated clips and images.

Rule ids of v0.1.0 (Turkish) are mapped to the current ids with LEGACY_RULE_IDS (old id -> new id).

Human texts (names, descriptions, labels) are English source texts marked with N_ or composed from such texts;
they are translated with T() where they are shown.
"""
from __future__ import annotations

import os
import time

from . import vault
from .i18n import N_

CATEGORIES = {
    "ppe": N_("PPE"),
    "area": N_("Area control"),
    "behavior": N_("Behavior"),
    "vehicle": N_("Vehicles"),
    "housekeeping": N_("Housekeeping"),
    "ergonomics": N_("Ergonomics"),
}

# zone types (drawn in the camera settings)
ZONE_TYPES = {
    "ppe": N_("PPE-required area"),
    "restricted": N_("Restricted / danger zone"),
    "machine": N_("Machine zone"),
    "electrical": N_("Electrical hazard"),
    "collision": N_("Collision-risk zone"),
    "headcount": N_("Headcount area"),
    "walkway": N_("Pedestrian walkway"),
    "vehicle_lane": N_("Vehicle lane"),
    "no_vehicle": N_("No-vehicle zone"),
    "height": N_("Work-at-height area"),
    "no_climb": N_("No-climbing surface (rack, machine)"),
    "aisle": N_("Keep-clear aisle"),
    "ladder": N_("Ladder"),
}

# classes of the object model -> rule class codes
CLASS_NAMES = {
    "hard_hat": N_("hard hat"), "vest": N_("hi-vis vest"), "gloves": N_("gloves"), "safety_glasses": N_("safety glasses"),
    "safety_shoes": N_("safety shoes"), "apron": N_("apron"), "face_mask": N_("face mask"), "face_shield": N_("face shield"),
    "hearing_protection": N_("hearing protection"), "harness": N_("safety harness"), "coveralls": N_("protective coveralls"),
    "sleeve_guards": N_("sleeve guards"), "respirator": N_("respirator"),
}
_ARTICLE = {"hard_hat": "a ", "vest": "a ", "apron": "an ", "face_mask": "a ", "face_shield": "a ", "harness": "a ", "respirator": "a "}

# v0.1.0 rule ids -> current rule ids (stored configs, API filters and queue records that escaped the migration)
LEGACY_RULE_IDS = {
    "baret": "hard_hat", "yelek": "vest", "eldiven": "gloves", "gozluk": "safety_glasses", "bot": "safety_shoes", "onluk": "apron",
    "maske": "face_mask", "siper": "face_shield", "kulaklik": "hearing_protection", "kemer": "harness", "tulum": "coveralls",
    "kolluk": "sleeve_guards", "carpisma_bolgesi": "collision_zone", "makine_yetkisiz": "unauthorized_in_machine_zone",
    "en_cok_kisi": "too_many_people", "en_az_kisi": "too_few_people", "yasak_nesne": "prohibited_object",
    "yasak_bolge": "restricted_zone", "saatli_bolge": "after_hours_entry", "bos_kalmamali": "unattended_area",
    "tirmanma": "climbing", "kalabalik": "crowding", "elektrik_bolgesi": "electrical_zone", "uc_nokta": "three_point_contact",
    "kosma": "running", "yaya_yolu_disi": "off_walkway", "kisi_yakinligi": "people_proximity", "yuksekte_calisma": "work_at_height",
    "dusme": "person_down", "arac_yol_disi": "vehicle_off_lane", "arac_yasak_bolge": "vehicle_in_restricted_zone",
    "arac_yaya": "vehicle_pedestrian", "arac_arac": "vehicle_vehicle", "ters_yon": "wrong_way", "arac_yolu_engel": "lane_obstruction",
    "gecit_engel": "blocked_aisle", "sahipsiz_nesne": "abandoned_object", "reba": "reba", "rula": "rula",
}


VALIDATION_NOTES: dict[str, str] = {  # result of the person attribute model on real photos not used in training
    "apron": N_("On real photos it found 105 of 106 people wearing an apron; it counted 4 of 491 people without an apron as wearing one."),
    "coveralls": N_("On real photos it found 120 of 127 people wearing coveralls (11 of 18 in a separate stock photo set); "
                    "it counted 5 of 512 people without coveralls as wearing them."),
    "sleeve_guards": N_("No real photos of sleeve guards could be found; it was trained on generated images only. It counted 3 of 21 real "
                        "people without sleeve guards as wearing them; it takes gloves that reach the elbow for sleeve guards."),
    "hearing_protection": N_("On real photos it found 62 of 73 people wearing hearing protection (it raises a false alert for the ones it misses); "
                             "it counted 9 of 333 people without hearing protection as wearing it."),
}


def _ppe_rule(rule_id, cls, name, severity, status, evidence="negative"):
    k = {"id": rule_id, "name": name, "category": "ppe", "mechanism": "ppe", "class": cls, "evidence": evidence, "severity": severity,
         "serious": False, "duration_s": 2.0, "status": status,
         "description": f"Person without {_ARTICLE.get(cls, '')}{CLASS_NAMES[cls]} in a PPE-required area."}
    if evidence == "absence":  # the model never says "not worn"; if the person is clearly visible and no protection is found, it counts as missing
        k.update(duration_s=4.0, off_by_default=True, min_height=150,  # a person shorter than this (pixels) is not evaluated
                 description=k["description"] + " Works with the add-on model: if the person is clearly visible and no protection is found, "
                                                "it counts as missing.")
    if evidence == "attribute":  # classifier looking at the person crop: both "worn" and "not worn" come from the model
        k.update(duration_s=3.0, off_by_default=True,
                 description=k["description"] + " Works with the person attribute model (when the person is at least 110 pixels tall in the image); "
                                                "may be wrong for people seen from behind.")
    if rule_id in VALIDATION_NOTES:
        k["description"] += " " + VALIDATION_NOTES[rule_id]
    return k


RULES: list[dict] = [
    # ---- personal protection (12)
    _ppe_rule("hard_hat", "hard_hat", N_("No hard hat"), 4, "ready"),
    _ppe_rule("vest", "vest", N_("No hi-vis vest"), 3, "ready"),
    _ppe_rule("gloves", "gloves", N_("No gloves"), 2, "ready"),
    _ppe_rule("safety_glasses", "safety_glasses", N_("No safety glasses"), 3, "ready"),
    _ppe_rule("safety_shoes", "safety_shoes", N_("No safety shoes"), 2, "ready"),
    _ppe_rule("apron", "apron", N_("No apron"), 2, "ready", "attribute"),
    _ppe_rule("face_mask", "face_mask", N_("No face mask"), 2, "ready", "absence"),
    _ppe_rule("face_shield", "face_shield", N_("No face shield"), 3, "ready", "absence"),
    _ppe_rule("hearing_protection", "hearing_protection", N_("No hearing protection"), 2, "limited", "attribute"),
    {**_ppe_rule("harness", "harness", N_("No safety harness"), 5, "limited", "attribute"), "zone_type": "height", "serious": True,
     "description": N_("Person without a full-body safety harness in a zone drawn as a work-at-height area. Works with the person attribute "
                       "model (when the person is at least 110 pixels tall in the image). On real photos not used in training it found 348 of "
                       "354 people wearing a harness; it counted 10 of 574 people without a harness as wearing one (no alert is raised for "
                       "them). In the sample press-shop clip it still takes one of two operators in striped vests, leaning over the bench, "
                       "to be wearing a harness.")},
    _ppe_rule("coveralls", "coveralls", N_("No protective coveralls"), 2, "limited", "attribute"),
    _ppe_rule("sleeve_guards", "sleeve_guards", N_("No sleeve guards"), 2, "limited", "attribute"),
    # (respirator: same mechanism as the face mask rule; enabled when a separate class is added)


    # ---- area control (8)
    {"id": "collision_zone", "name": N_("Person and vehicle in a collision-risk zone"), "category": "area", "mechanism": "co_presence",
     "zone_type": "collision", "severity": 5, "serious": True, "duration_s": 0.5, "status": "ready",
     "description": N_("A pedestrian and a vehicle are in the zone where the routes cross at the same time.")},
    {"id": "unauthorized_in_machine_zone", "name": N_("Unauthorized person in a machine zone"), "category": "area", "mechanism": "zone",
     "subject": "person", "zone_type": "machine", "authorized_color": True, "severity": 4, "serious": True, "duration_s": 1.5, "status": "ready",
     "description": N_("Someone not wearing the authorized clothing color entered the machine zone (no identity recognition; "
                       "people are told apart by clothing color).")},
    {"id": "too_many_people", "name": N_("Too many people in the area"), "category": "area", "mechanism": "count", "zone_type": "headcount",
     "max": 4, "severity": 3, "serious": False, "duration_s": 3.0, "status": "ready",
     "description": N_("The number of people in the area is above the safe limit.")},
    {"id": "too_few_people", "name": N_("Too few people in the area"), "category": "area", "mechanism": "count", "zone_type": "headcount",
     "min": 2, "severity": 3, "serious": False, "duration_s": 10.0, "status": "ready", "off_by_default": True,
     "description": N_("The headcount fell below the minimum the job requires (e.g. work that must not be done alone).")},
    {"id": "prohibited_object", "name": N_("Prohibited object"), "category": "area", "mechanism": "object", "zone_type": "restricted",
     "classes": ["phone", "ladder"], "severity": 3, "serious": False, "duration_s": 2.0, "status": "ready", "off_by_default": True,
     "description": N_("An object that must not be in the restricted zone was seen (cell phone, ladder). Works with the add-on model.")},
    {"id": "restricted_zone", "name": N_("Restricted zone entry"), "category": "area", "mechanism": "zone", "subject": "person",
     "zone_type": "restricted", "severity": 5, "serious": True, "duration_s": 1.0, "status": "ready",
     "description": N_("Someone entered the danger zone around running equipment.")},
    {"id": "after_hours_entry", "name": N_("Entry outside permitted hours"), "category": "area", "mechanism": "zone", "subject": "person",
     "zone_type": "restricted", "hours": ["22:00-06:00"], "severity": 3, "serious": False, "duration_s": 2.0, "status": "ready",
     "off_by_default": True, "description": N_("Someone is in the area during hours when entry is not permitted.")},
    {"id": "unattended_area", "name": N_("Unattended area"), "category": "area", "mechanism": "count", "zone_type": "headcount", "min": 1,
     "severity": 3, "serious": False, "duration_s": 20.0, "status": "ready", "off_by_default": True,
     "description": N_("An area that must be attended was left empty.")},

    # ---- behavior (8)
    {"id": "climbing", "name": N_("Climbing"), "category": "behavior", "mechanism": "zone", "subject": "person", "zone_type": "no_climb",
     "severity": 4, "serious": True, "duration_s": 1.0, "status": "ready",
     "description": N_("Person climbing a rack, machine or structure (the surface that must not be climbed is drawn as a zone).")},
    {"id": "crowding", "name": N_("Crowding in a confined area"), "category": "behavior", "mechanism": "count", "zone_type": "headcount",
     "max": 6, "severity": 2, "serious": False, "duration_s": 5.0, "status": "ready", "off_by_default": True,
     "description": N_("Many people gathered in a confined area at the same time.")},
    {"id": "electrical_zone", "name": N_("Electrical hazard zone entry"), "category": "behavior", "mechanism": "zone", "subject": "person",
     "zone_type": "electrical", "severity": 5, "serious": True, "duration_s": 1.0, "status": "ready",
     "description": N_("Entry into a zone where live equipment can be touched.")},
    {"id": "three_point_contact", "name": N_("No three-point contact on a ladder"), "category": "behavior", "mechanism": "three_point",
     "severity": 3, "serious": False, "duration_s": 2.0, "status": "ready", "off_by_default": True,
     "description": N_("Fewer than three of the hands and feet of the person on the ladder are on the ladder (from the pose). The ladder is "
                       "a drawn ladder zone or a ladder found by the add-on model. Contact itself cannot be seen: a hand or foot over the "
                       "ladder counts as contact; a hand holding a load such as a box does not.")},
    {"id": "running", "name": N_("Running"), "category": "behavior", "mechanism": "speed", "threshold": 1.4, "severity": 2, "serious": False,
     "duration_s": 0.8, "status": "ready", "description": N_("Person running where there is a risk of slipping or collision. Speed is measured relative to "
                                                             "the person's height.")},
    {"id": "off_walkway", "name": N_("Walking outside the pedestrian walkway"), "category": "behavior", "mechanism": "off_lane", "subject": "person",
     "zone_type": "walkway", "severity": 3, "serious": False, "duration_s": 2.0, "status": "ready",
     "description": N_("Person walking outside the drawn pedestrian walkway.")},
    {"id": "people_proximity", "name": N_("People too close together"), "category": "behavior", "mechanism": "proximity", "a": "person",
     "b": "person", "threshold": 0.6, "severity": 2, "serious": False, "duration_s": 3.0, "status": "ready", "off_by_default": True,
     "description": N_("People are dangerously close to each other or to a hazard.")},
    {"id": "work_at_height", "name": N_("Work at height"), "category": "behavior", "mechanism": "zone", "subject": "person",
     "zone_type": "height", "severity": 5, "serious": True, "duration_s": 2.0, "status": "ready",
     "description": N_("A person is in a zone drawn as a work-at-height area (whether they wear a harness is not checked).")},

    {"id": "person_down", "name": N_("Person down"), "category": "behavior", "mechanism": "posture", "posture": "lying", "severity": 5,
     "serious": True, "duration_s": 2.0, "status": "ready", "off_by_default": True,
     "description": N_("Person lying on the ground: torso close to horizontal and box wider than tall (from the pose). A person lying "
                       "toward or away from the camera may be missed.")},

    # ---- vehicles (5)
    {"id": "vehicle_off_lane", "name": N_("Vehicle outside its lane"), "category": "vehicle", "mechanism": "off_lane", "subject": "vehicle",
     "zone_type": "vehicle_lane", "severity": 3, "serious": False, "duration_s": 2.0, "status": "ready",
     "description": N_("The vehicle is outside the lane assigned to it.")},
    {"id": "vehicle_in_restricted_zone", "name": N_("Vehicle in a no-vehicle zone"), "category": "vehicle", "mechanism": "zone",
     "subject": "vehicle", "zone_type": "no_vehicle", "severity": 4, "serious": True, "duration_s": 1.0, "status": "ready",
     "description": N_("A vehicle entered an area that vehicles must not enter.")},
    {"id": "vehicle_pedestrian", "name": N_("Vehicle–pedestrian proximity"), "category": "vehicle", "mechanism": "proximity", "a": "vehicle",
     "b": "person", "threshold": 1.0, "severity": 5, "serious": True, "duration_s": 0.5, "status": "ready",
     "description": N_("A vehicle is close enough to a worker on foot to risk hitting them (threshold: a multiple of the person's height).")},
    {"id": "vehicle_vehicle", "name": N_("Vehicle–vehicle proximity"), "category": "vehicle", "mechanism": "proximity", "a": "vehicle",
     "b": "vehicle", "threshold": 1.0, "severity": 3, "serious": False, "duration_s": 1.0, "status": "limited",
     "description": N_("Two vehicles came unsafely close (threshold: the gap between the vehicles in the image, as a multiple of the vehicle "
                       "height). In testing, vehicles approaching head-on and at a crossing were caught; two forklifts standing side by side "
                       "were missed because they were counted as one vehicle.")},
    {"id": "wrong_way", "name": N_("Wrong-way driving"), "category": "vehicle", "mechanism": "wrong_way", "subject": "vehicle",
     "zone_type": "vehicle_lane", "severity": 4, "serious": True, "duration_s": 1.0, "status": "ready",
     "description": N_("The vehicle is moving against the set direction of the lane.")},

    # ---- housekeeping (3)
    {"id": "lane_obstruction", "name": N_("Obstruction in a vehicle lane"), "category": "housekeeping", "mechanism": "object",
     "zone_type": "vehicle_lane", "classes": ["pallet", "box", "barrel"], "severity": 3, "serious": False, "duration_s": 20.0,
     "status": "ready", "off_by_default": True,
     "description": N_("An object left standing in a vehicle lane (pallet, box, barrel). Works with the add-on model.")},
    {"id": "blocked_aisle", "name": N_("Blocked aisle"), "category": "housekeeping", "mechanism": "object", "zone_type": "aisle",
     "classes": ["pallet", "box", "barrel"], "severity": 3, "serious": False, "duration_s": 20.0, "status": "ready", "off_by_default": True,
     "description": N_("An object left standing in a keep-clear aisle (pallet, box, barrel). Works with the add-on model.")},
    {"id": "abandoned_object", "name": N_("Abandoned object"), "category": "housekeeping", "mechanism": "object", "classes": ["pallet", "box"],
     "unattended": True, "severity": 2, "serious": False, "duration_s": 30.0, "status": "ready", "off_by_default": True,
     "description": N_("An object standing in place with nobody near it (pallet, box, barrel). Works with the add-on model.")},

    # ---- ergonomics (2)
    {"id": "reba", "name": N_("Awkward posture (REBA)"), "category": "ergonomics", "mechanism": "ergonomics", "threshold": 7, "severity": 3,
     "serious": False, "duration_s": 4.0, "status": "ready", "off_by_default": True,
     "description": N_("The person's REBA posture score stayed above the threshold. The score is estimated from the joint angles in the "
                       "image; because the load, coupling and repetition scores are not added, it comes out 1-3 lower than the full REBA "
                       "score (8-10 is high risk on the full score), so the threshold is 7.")},
    {"id": "rula", "name": N_("Upper-body ergonomic risk (RULA)"), "category": "ergonomics", "mechanism": "none", "severity": 2, "serious": False,
     "status": "off", "description": N_("Upper-body posture score for reaching, assembly and sorting work. Not implemented yet.")},
]

RULE = {k["id"]: k for k in RULES}

# The panel shows, for each rule, how many test situations were caught and how many false alarms occurred.
RULE_VALIDATION_SUMMARY = {
    "hard_hat": {"caught": 3, "expected": 3, "false_alarms": 0, "checked": 8},
    "vest": {"caught": 2, "expected": 2, "false_alarms": 0, "checked": 7},
    "wrong_way": {"caught": 1, "expected": 1, "false_alarms": 0, "checked": 2},
    "vehicle_pedestrian": {"caught": 2, "expected": 2, "false_alarms": 0, "checked": 2},
    "off_walkway": {"caught": 2, "expected": 2, "false_alarms": 0, "checked": 3},
    "vehicle_off_lane": {"caught": 1, "expected": 1, "false_alarms": 0, "checked": 1},
    "restricted_zone": {"caught": 1, "expected": 1, "false_alarms": 0, "checked": 1},
    "unauthorized_in_machine_zone": {"caught": 2, "expected": 2, "false_alarms": 0, "checked": 1},
    "climbing": {"caught": 1, "expected": 1, "false_alarms": 0, "checked": 1},
    "running": {"caught": 1, "expected": 1, "false_alarms": 0, "checked": 3},
    "people_proximity": {"caught": 1, "expected": 1, "false_alarms": 0, "checked": 1},
    "blocked_aisle": {"caught": 1, "expected": 1, "false_alarms": 0, "checked": 1},
    "too_many_people": {"caught": 1, "expected": 1, "false_alarms": 0, "checked": 1},
    "crowding": {"caught": 1, "expected": 1, "false_alarms": 0, "checked": 1},
    "prohibited_object": {"caught": 2, "expected": 2, "false_alarms": 0, "checked": 2},
    "vehicle_in_restricted_zone": {"caught": 1, "expected": 1, "false_alarms": 0, "checked": 1},
    "collision_zone": {"caught": 1, "expected": 1, "false_alarms": 0, "checked": 1},
    "vehicle_vehicle": {"caught": 2, "expected": 3, "false_alarms": 0, "checked": 2},
    "gloves": {"caught": 4, "expected": 4, "false_alarms": 0, "checked": 3},
    "safety_shoes": {"caught": 2, "expected": 2, "false_alarms": 0, "checked": 3},
    "electrical_zone": {"caught": 1, "expected": 1, "false_alarms": 0, "checked": 1},
    "work_at_height": {"caught": 3, "expected": 3, "false_alarms": 0, "checked": 1},
    "after_hours_entry": {"caught": 1, "expected": 1, "false_alarms": 0, "checked": 1},
    "too_few_people": {"caught": 1, "expected": 1, "false_alarms": 0, "checked": 1},
    "unattended_area": {"caught": 1, "expected": 1, "false_alarms": 0, "checked": 1},
    "abandoned_object": {"caught": 1, "expected": 1, "false_alarms": 0, "checked": 1},
    "lane_obstruction": {"caught": 1, "expected": 1, "false_alarms": 0, "checked": 1},
    "person_down": {"caught": 1, "expected": 1, "false_alarms": 0, "checked": 4},
    "reba": {"caught": 1, "expected": 1, "false_alarms": 0, "checked": 4},
    "three_point_contact": {"caught": 1, "expected": 1, "false_alarms": 0, "checked": 1},
    "safety_glasses": {"caught": 2, "expected": 2, "false_alarms": 0, "checked": 1},
    "face_shield": {"caught": 2, "expected": 2, "false_alarms": 0, "checked": 1},
    "apron": {"caught": 5, "expected": 5, "false_alarms": 0, "checked": 3},
    "hearing_protection": {"caught": 5, "expected": 5, "false_alarms": 0, "checked": 4},
    "coveralls": {"caught": 4, "expected": 4, "false_alarms": 0, "checked": 2},
    "sleeve_guards": {"caught": 2, "expected": 2, "false_alarms": 0, "checked": 2},
    "face_mask": {"caught": 3, "expected": 3, "false_alarms": 0, "checked": 3},
    "harness": {"caught": 3, "expected": 3, "false_alarms": 0, "checked": 3},
}
for _k in RULES:
    if _k["id"] in RULE_VALIDATION_SUMMARY:
        _k["validation"] = RULE_VALIDATION_SUMMARY[_k["id"]]

# Which model provides which classes. An add-on model is loaded only when a rule that needs it is on.
MODEL_PLAN = [
    {"file": "isg-nesne-1280.onnx", "confidence": 0.25,
     "classes": ["person", "hard_hat", "hard_hat_missing", "vest", "vest_missing", "gloves", "gloves_missing",
                 "safety_glasses", "safety_glasses_missing", "safety_shoes", "safety_shoes_missing", "forklift"]},
    {"file": "isg-ek-1280.onnx", "confidence": 0.3, "class_confidence": {"ladder": 0.6},  # rack uprights can come out as "ladder" at 0.3-0.5 confidence
     "classes": ["face_shield", "hearing_protection", "face_mask", "pallet", "box", "barrel", "ladder", "phone"],
     "rules": ["face_shield", "hearing_protection", "face_mask", "prohibited_object", "lane_obstruction", "blocked_aisle",
               "abandoned_object", "three_point_contact"],
     "optional": ["three_point_contact"]},  # the three-point rule also works without the add-on model, with a drawn ladder zone
    # person attribute model: apron, coveralls, sleeve guards, safety harness, hearing protection from the person crop
    {"file": "isg-oznitelik.onnx", "kind": "attributes", "rules": ["apron", "coveralls", "sleeve_guards", "harness", "hearing_protection"]},
]


# Pose model (ISG Pose block): rules that cannot be evaluated without keypoints
POSE_MODEL = {"file": "isg-iskelet.onnx",
              "rules": [k["id"] for k in RULES if k["mechanism"] in ("posture", "ergonomics", "three_point") or k.get("evidence") == "absence"]}

MODEL_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "models")
MODEL_FILES = [m["file"] for m in MODEL_PLAN] + [POSE_MODEL["file"]]


def model_lock(data_dir: str | None, model_dir: str | None = None) -> dict:
    """Returns whether the model package can be used."""
    return vault.status(model_dir or MODEL_DIR, MODEL_FILES, data_dir)


def activate_models(data_dir: str, code: str, model_dir: str | None = None) -> None:
    """Saves the model key on this computer with an activation code (vault.activate)."""
    vault.activate(model_dir or MODEL_DIR, data_dir, code, MODEL_FILES)
_MISSING_CACHE = {"t": 0.0, "model_dir": None, "v": {}}


def missing_models(model_dir: str | None = None) -> dict[str, str]:
    """Rules that cannot run because a model file is missing: {rule id: name of the missing file}.

    Without the main model no rule works. Rules that work with the attribute model (evidence: attribute) depend only on that
    model, the others on the detection model that lists them, and those that need keypoints also on the pose model.
    The result is kept for five seconds (so the disk is not checked on every frame)."""
    model_dir = model_dir or MODEL_DIR
    now = time.monotonic()
    if _MISSING_CACHE["model_dir"] == model_dir and now - _MISSING_CACHE["t"] < 5.0:
        return _MISSING_CACHE["v"]
    out: dict[str, str] = {}
    for m in MODEL_PLAN + [POSE_MODEL]:
        if vault.model_exists(os.path.join(model_dir, m["file"])):
            continue
        attributes = m.get("kind") == "attributes"
        for rule_id in m.get("rules") or [k["id"] for k in RULES]:
            k = RULE.get(rule_id)
            if k is None or rule_id in m.get("optional", ()) or (m.get("rules") and (k.get("evidence") == "attribute") != attributes):
                continue
            out.setdefault(rule_id, m["file"])
    _MISSING_CACHE.update(t=now, model_dir=model_dir, v=out)
    return out


def model_plan(camera: dict) -> list[dict]:
    """Models to load for the rules that are on for this camera."""
    on = {k["id"] for k in resolve(camera)}
    return [m for m in MODEL_PLAN if not m.get("rules") or on & set(m["rules"])]


def resolve(camera: dict) -> list[dict]:
    """Merges the "rules" list of the camera settings with the catalog.

    In the settings: "rules": [{"id": "hard_hat"}, {"id": "too_many_people", "max": 3, "zone": "Press line"}, ...]
    Without a list: every rule that is on by default and matches the drawn zone types.
    Rules whose model file is missing (missing_models) are left out even when on; the panel shows them as "model file missing".
    v0.1.0 rule ids are accepted (LEGACY_RULE_IDS).
    """
    types = {b.get("type") for b in camera.get("zones") or []}
    requested = camera.get("rules")
    missing = missing_models()
    out = []
    if requested is None:
        for k in RULES:
            if k["status"] in ("off", "weak") or k.get("off_by_default") or k["id"] in missing:
                continue
            if k.get("zone_type") and k["zone_type"] not in types:
                continue
            if k["mechanism"] == "ppe" and not k.get("zone_type") and "ppe" not in types and not camera.get("ppe_everywhere"):
                continue
            out.append(dict(k))
        return out
    for i in requested:
        rule_id = str(i.get("id", ""))
        base = RULE.get(LEGACY_RULE_IDS.get(rule_id, rule_id))
        if base is None or base["status"] == "off" or i.get("disabled") or base["id"] in missing:
            continue
        out.append({**base, **{a: v for a, v in i.items() if a != "disabled"}, "id": base["id"]})
    return out


def likelihood(duration_s: float, repeat: int = 1) -> int:
    """1-5: how long the event lasted and how many times the same rule has fired in this recording."""
    p = 1 + (duration_s >= 3) + (duration_s >= 10) + (duration_s >= 30)
    p += (repeat >= 3) + (repeat >= 8)
    return int(min(5, p))


def risk(severity: int, likelihood: int) -> int:
    """Risk score = severity x likelihood (1-25)."""
    return int(severity) * int(likelihood)


def level(score: int) -> str:
    return "critical" if score >= 15 else "high" if score >= 9 else "medium" if score >= 4 else "low"
