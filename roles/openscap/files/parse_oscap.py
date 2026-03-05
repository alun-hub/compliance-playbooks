#!/usr/bin/env python3
"""
Parsare för OpenSCAP XCCDF-resultatfiler.
Skriver JSON till stdout för konsumtion av Ansible.
"""

import sys
import json
import xml.etree.ElementTree as ET

# XCCDF 1.2 namespace
NS = {"xccdf": "http://checklists.nist.gov/xccdf/1.2"}

SEVERITY_ORDER = {"high": 0, "medium": 1, "low": 2, "unknown": 3}


def parse(result_file: str) -> dict:
    tree = ET.parse(result_file)
    root = tree.getroot()

    test_result = root.find(".//xccdf:TestResult", NS)
    if test_result is None:
        # Fallback utan namespace (äldre oscap-versioner)
        test_result = root.find(".//TestResult")
    if test_result is None:
        return {"error": "Hittade inte TestResult i XML", "score": 0, "pass": 0, "fail": 0, "failed_rules": []}

    score_elem = test_result.find("xccdf:score", NS)
    score = round(float(score_elem.text), 1) if score_elem is not None else 0.0

    pass_count = 0
    fail_count = 0
    notapplicable_count = 0
    failed_rules = []

    for rule_result in test_result.findall("xccdf:rule-result", NS):
        result_elem = rule_result.find("xccdf:result", NS)
        if result_elem is None:
            continue

        result_text = result_elem.text.strip()
        rule_id = rule_result.get("idref", "")
        severity = rule_result.get("severity", "unknown")

        if result_text == "pass":
            pass_count += 1
        elif result_text == "fail":
            fail_count += 1
            failed_rules.append({
                "id": rule_id,
                "severity": severity,
            })
        elif result_text == "notapplicable":
            notapplicable_count += 1

    # Sortera efter allvarlighetsgrad
    failed_rules.sort(key=lambda r: SEVERITY_ORDER.get(r["severity"], 3))

    return {
        "score": score,
        "pass": pass_count,
        "fail": fail_count,
        "notapplicable": notapplicable_count,
        "failed_rules": failed_rules,
        "high_severity_failures": sum(1 for r in failed_rules if r["severity"] == "high"),
    }


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(json.dumps({"error": "Användning: parse_oscap.py <result.xml>"}))
        sys.exit(1)

    try:
        result = parse(sys.argv[1])
    except ET.ParseError as e:
        result = {"error": f"XML-parsfel: {e}", "score": 0, "pass": 0, "fail": 0, "failed_rules": []}
    except FileNotFoundError:
        result = {"error": f"Filen hittades inte: {sys.argv[1]}", "score": 0, "pass": 0, "fail": 0, "failed_rules": []}

    print(json.dumps(result))
