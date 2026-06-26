# Copyright (c) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

import requests, json

BASE_URL = "http://localhost:9001"

def test_add_workload():
    payloads = [
        {
            "priority": "critical",
            "payload": {"pid": 12345, "task": "high_priority_service"}
        },
    ]

    for data in payloads:
        try:
            resp = requests.post(
                f"{BASE_URL}/add_workload",
                json=data,
                timeout=3
            )
            print(f"Response: {resp.status_code} - {resp.json()}")
        except requests.exceptions.RequestException as e:
            print(f"Request failed: {str(e)}")


def test_get_apps():
    """Test get apps list API."""
    try:
        resp = requests.get(
            f"{BASE_URL}/app/get_apps",
            timeout=5
        )
        print("\n[GET /app/get_apps]")
        print(f"Status: {resp.status_code}")
        print("Response:")
        print(json.dumps(resp.json(), indent=2))

        data = resp.json()
        assert isinstance(data.get("data", []), list), "Response data should be a list"
        print("✓ Valid response structure")
    except Exception as e:
        print(f"Test failed: {str(e)}")


def test_set_priority():
    """Test set priority API."""
    test_cases = [
        {
            "name": "Normal priority set",
            "payload": {"app_id": "gnome-privacy-panel.desktop", "priority": 60, "cgroup": "system"},
            "expected": 60
        },
        {
            "name": "Non-existent app",
            "payload": {"app_id": "nonexistent.app", "priority": 80, "cgroup": "user"},
            "expected": None
        }
    ]

    for case in test_cases:
        try:
            print(f"\n[POST /app/set_priority] {case['name']}")
            resp = requests.post(
                f"{BASE_URL}/app/set_priority",
                json=case["payload"],
                timeout=5
            )
            print(f"Status: {resp.status_code}")
            print("Response:")
            print(json.dumps(resp.json(), indent=2))

            if case["expected"]:
                assert resp.json().get("code", 1) == 0, "Expected success response"
                print(f"✓ Priority set to {case['expected']}")
        except Exception as e:
            print(f"Test case failed: {str(e)}")


def test_get_priority():
    """Test get priority by app_id API."""
    try:
        print("\n[POST /app/get_priority_data]")
        test_app_id = "org.gnome.Calculator.desktop"
        app_name = "Calculator"

        resp = requests.post(
            f"{BASE_URL}/app/get_priority_data",
            json={"app_id": test_app_id, "app_name": app_name},
            timeout=5
        )

        print(f"Status: {resp.status_code}")
        print("Response:")
        print(json.dumps(resp.json(), indent=2))

        data = resp.json()
        if resp.status_code == 200:
            if data.get("data") and data["data"]["app_id"] == test_app_id:
                print(f"✓ Found priority for {test_app_id}: {data['data']['priority']}")
            else:
                print(f"✗ Priority data for {test_app_id} not found in response")
        elif resp.status_code == 404:
            print(f"✗ App {test_app_id} not found")
        else:
            print(f"✗ Unexpected response status: {resp.status_code}")

    except Exception as e:
        print(f"Test failed: {str(e)}")


def test_set_control():
    """Test set app control API."""
    test_cases = [
        {
            "name": "Firefox",
            "payload": {
                "app_name": "Firefox",
                "app_id": "firefox_firefox.desktop",
                "controlled": True,
                "priority": 50
            }
        },
        {
            "name": "Calculator",
            "payload": {
                "app_name": "Calculator",
                "app_id": "org.gnome.Calculator.desktop",
                "controlled": True,
                "priority": 60
            }
        },
        {
            "name": "Gedit",
            "payload": {
                "app_name": "gedit",
                "app_id": "org.gnome.gedit.desktop",
                "controlled": True,
                "priority": 30
            }
        }
    ]

    for case in test_cases:
        try:
            print(f"\n[POST /app/set_to_control] {case['name']}")
            resp = requests.post(
                f"{BASE_URL}/app/set_to_control",
                json=case["payload"],
                timeout=5
            )
            print(f"Status: {resp.status_code}")
            print("Response:")
            print(json.dumps(resp.json(), indent=2))
        except Exception as e:
            print(f"Test failed: {str(e)}")


def test_submit_task():
    """Test submit task API."""
    try:
        print("\n[POST /submit_task] Submitting task...")
        resp = requests.post(
            f"{BASE_URL}/app/submit_task",
            json={
                "task_id": "test_123",
            },
            timeout=5
        )
        print(f"Status: {resp.status_code}")
        print("Response:", resp.json())

    except Exception as e:
        print(f"Test failed: {str(e)}")


if __name__ == "__main__":
    # test_add_workload()
    # test_get_apps()
    # test_set_priority()
    # test_get_priority()
    # test_set_control()
    test_submit_task()
