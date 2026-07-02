# 单元测试质量审查报告

## 问题总结

原有167个单元测试全部通过，但存在以下质量问题：

---

## 1. 测试覆盖了错误行为 (test_app_utils.py)

**文件**: `test_app_utils.py:56-57`

```python
def test_env_prefix_skipped(self):
    result = self.func("App", "env FOO=bar /usr/bin/app")
    assert result == "foo=bar"  # 把bug当成正确行为了!
```

**问题**: 测试断言的是当前（有bug的）行为，而不是正确行为。正确结果应该是 `"app"`。
这样的测试不但不能发现bug，反而会在修复bug后失败，阻碍修复。

---

## 2. API端点测试是自我测试 (test_api_endpoints.py)

**问题**: `test_api_endpoints.py` 没有导入或测试 `BalanceService.py` 中的真实路由处理函数。
它在 fixture 中**重新实现了路由逻辑**（手写了 login、add_workload 等函数），然后测试自己写的实现。

**后果**: 
- 如果 BalanceService.py 的逻辑有 bug（如 `get_priority_data` 的 IndexError），这些测试完全无法发现
- 任何 BalanceService.py 的代码变更都不会导致这些测试失败
- 等于在测 mock 本身，不是在测产品代码

---

## 3. Controller测试过度mock (test_controller.py)

**TestIOController 示例**:
```python
def test_get_disk_id(self, io_ctl):
    # 全是mock，只断言 "对象不为None"
    assert io_ctl is not None

def test_set_disk_io_throttle_format(self, io_ctl):
    # 同样只断言 "对象不为None"
    assert io_ctl is not None
```

**问题**: 这些测试没有验证任何实际逻辑。它们永远通过，等于不存在。

---

## 4. 缺失的关键测试场景

以下关键逻辑路径完全没有被测试覆盖：

| 模块 | 未测试的关键逻辑 |
|------|-----------------|
| `BalanceService.py` | `get_priority_data` 无参时 IndexError |
| `BalanceService.py` | `set_to_control` 新增app + `check_app_running_status` |
| `app_utils.py` | `get_app_control_info` 大小写匹配 |
| `app_utils.py` | `update_app_status` 返回值检查 |
| `app_utils.py` | `adjust_oom_priority` 完整路径（含restore） |
| `controller.py` | `_set_resource_quota` 的 scope/service 匹配逻辑 |
| `balancer.py` | 压力升级/降级状态机转换 |
| `config.py` | `update_config_section` 并发更新冲突 |

---

## 5. 发现的真实代码 Bug

详见 `test_bugs_found.py`（5个失败测试 = 5个真实bug）：

1. **get_app_control_info() 大小写 bug** — name_map key 是 lower() 的，但查找时没有 lower
2. **DBStatus enum 判断错误** — `if not result` 无法检测 NOT_FOUND/FAILED
3. **get_priority_data IndexError** — 空参数时 conditions[0] 崩溃
4. **_get_executable_name env var bug** — KEY=VALUE 不被跳过，误认为可执行文件
5. **send_callback_notification 静默失败** — NOT_FOUND 时不打 warning

---

## 建议

1. `test_api_endpoints.py` 应该直接导入 BalanceService 的 Flask app 并 mock 底层依赖（DB、subprocess），而不是重写路由
2. `test_app_utils.py:57` 应该改为断言正确行为 (`assert result == "app"`)
3. `test_controller.py` 的 IOController 测试需要实际验证 throttle 命令构造逻辑
4. 所有 `test_bugs_found.py` 中暴露的 bug 应被修复，修复后对应测试应全部 PASS
