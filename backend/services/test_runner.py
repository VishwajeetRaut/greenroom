"""
Test-case harness for the technical interview coding problem (Two Sum).

Injects a hidden harness into the user's code that runs each test case and
prints one line per case: PASS | FAIL:<output> | ERROR:<message>.
"""

from __future__ import annotations
import json

# ── Test cases ────────────────────────────────────────────────────────────────

_VISIBLE: list[dict] = [
    {
        "id": 0,
        "label": "Example 1",
        "input_display": "nums = [2,7,11,15], target = 9",
        "nums": [2, 7, 11, 15],
        "target": 9,
        "expected": [0, 1],
    },
    {
        "id": 1,
        "label": "Example 2",
        "input_display": "nums = [3,2,4], target = 6",
        "nums": [3, 2, 4],
        "target": 6,
        "expected": [1, 2],
    },
    {
        "id": 2,
        "label": "Example 3",
        "input_display": "nums = [3,3], target = 6",
        "nums": [3, 3],
        "target": 6,
        "expected": [0, 1],
    },
]

_HIDDEN: list[dict] = [
    {"id": 3, "nums": [1, 2, 3, 4, 5],       "target": 9,  "expected": [3, 4]},
    {"id": 4, "nums": [-1, -2, -3, -4, -5],  "target": -8, "expected": [2, 4]},
    {"id": 5, "nums": [0, 4, 3, 0],           "target": 0,  "expected": [0, 3]},
    {"id": 6, "nums": [1, 3, 4, 2],           "target": 6,  "expected": [2, 3]},
]

_ALL = _VISIBLE + _HIDDEN


# ── Harness builders ───────────────────────────────────────────────────────────

def _cases_json() -> str:
    return json.dumps(
        [{"nums": t["nums"], "target": t["target"], "expected": t["expected"]} for t in _ALL]
    )


def build_harness(language: str, user_code: str) -> str | None:
    """Return user_code wrapped with a test harness, or None if unsupported."""
    cases = _cases_json()

    if language == "python":
        return f"""{user_code}

import json as _j
_c = {cases}
for _t in _c:
    try:
        _r = two_sum(list(_t["nums"]), _t["target"])
        _ok = sorted(list(_r)) == sorted(_t["expected"])
        print("PASS" if _ok else "FAIL:" + _j.dumps(list(_r)))
    except Exception as _e:
        print("ERROR:" + str(_e)[:200])
"""

    if language == "node":
        return f"""{user_code}
const _c={cases};
for(const _t of _c){{
  try{{
    const _r=twoSum([..._t.nums],_t.target);
    const _s=a=>[...a].sort((x,y)=>x-y);
    const _ok=JSON.stringify(_s(_r))===JSON.stringify(_s(_t.expected));
    console.log(_ok?"PASS":"FAIL:"+JSON.stringify(_r));
  }}catch(_e){{console.log("ERROR:"+(_e.message||String(_e)).slice(0,200));}}
}}
"""

    if language == "java":
        nums_arr  = "{" + ",".join("{" + ",".join(map(str, t["nums"])) + "}" for t in _ALL) + "}"
        tgts_arr  = "{" + ",".join(str(t["target"]) for t in _ALL) + "}"
        exp_arr   = "{" + ",".join("{" + ",".join(map(str, t["expected"])) + "}" for t in _ALL) + "}"
        return f"""{user_code}

class __Runner {{
    static boolean eq(int[] a, int[] b) {{
        int[] x=a.clone(),y=b.clone();
        java.util.Arrays.sort(x); java.util.Arrays.sort(y);
        return java.util.Arrays.equals(x,y);
    }}
    public static void main(String[] _args) {{
        Solution sol=new Solution();
        int[][] nums={nums_arr};
        int[] targets={tgts_arr};
        int[][] expected={exp_arr};
        for(int i=0;i<nums.length;i++){{
            try{{
                int[] r=sol.twoSum(nums[i].clone(),targets[i]);
                System.out.println(eq(r,expected[i])?"PASS":"FAIL:"+java.util.Arrays.toString(r));
            }}catch(Exception e){{System.out.println("ERROR:"+e.getMessage());}}
        }}
    }}
}}
"""

    if language in ("c++", "cpp", "gcc"):
        nums_vecs = ",".join("{" + ",".join(map(str, t["nums"])) + "}" for t in _ALL)
        tgts_vec  = ",".join(str(t["target"]) for t in _ALL)
        exp_vecs  = ",".join("{" + ",".join(map(str, t["expected"])) + "}" for t in _ALL)
        return f"""{user_code}

int main(){{
    Solution sol;
    std::vector<std::vector<int>> _nums={{{nums_vecs}}};
    std::vector<int> _tgts={{{tgts_vec}}};
    std::vector<std::vector<int>> _exp={{{exp_vecs}}};
    for(int i=0;i<(int)_nums.size();i++){{
        std::vector<int> _r=sol.twoSum(_nums[i],_tgts[i]);
        std::vector<int> _rs=_r,_es=_exp[i];
        std::sort(_rs.begin(),_rs.end()); std::sort(_es.begin(),_es.end());
        if(_rs==_es){{std::cout<<"PASS"<<std::endl;}}
        else{{std::cout<<"FAIL:["<<_r[0]<<","<<_r[1]<<"]"<<std::endl;}}
    }}
    return 0;
}}
"""

    return None  # unsupported language — caller falls back to raw execution


# ── Result parser ──────────────────────────────────────────────────────────────

def parse_results(stdout: str, stderr: str) -> dict:
    """
    Parse PASS/FAIL/ERROR lines produced by the harness into a structured
    response ready to send to the frontend.
    """
    lines = stdout.strip().splitlines() if stdout.strip() else []

    # Compile/import error: stderr present but no harness output at all
    if stderr and not lines:
        return {
            "status": "compile_error",
            "compile_error": stderr[:1500],
            "visible_tests": [],
            "hidden_tests": [],
            "passed": 0,
            "total": len(_ALL),
        }

    visible_results: list[dict] = []
    hidden_results: list[dict] = []
    passed = 0

    for i, tc in enumerate(_ALL):
        line = lines[i] if i < len(lines) else ""

        if line == "PASS":
            tc_passed, output_val, error_val = True, json.dumps(tc["expected"]), None
        elif line.startswith("FAIL:"):
            tc_passed, output_val, error_val = False, line[5:], None
        elif line.startswith("ERROR:"):
            tc_passed, output_val, error_val = False, None, line[6:]
        else:
            tc_passed, output_val, error_val = False, None, (stderr[:500] if stderr else "No output")

        if tc_passed:
            passed += 1

        if i < len(_VISIBLE):
            entry: dict = {
                "id": tc["id"],
                "label": tc.get("label", f"Test {i + 1}"),
                "input": tc.get("input_display", f"nums={tc['nums']}, target={tc['target']}"),
                "expected": json.dumps(tc["expected"]),
                "output": output_val,
                "passed": tc_passed,
            }
            if error_val:
                entry["error"] = error_val
            visible_results.append(entry)
        else:
            hidden_results.append({"id": tc["id"], "passed": tc_passed})

    any_error = any(r.get("error") or (not r["passed"] and r.get("output") is None) for r in visible_results)
    all_passed = passed == len(_ALL)

    status = "accepted" if all_passed else ("runtime_error" if any_error else "wrong_answer")

    return {
        "status": status,
        "visible_tests": visible_results,
        "hidden_tests": hidden_results,
        "passed": passed,
        "total": len(_ALL),
    }
