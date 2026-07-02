# Intel XPU SmarTune 测试计划（Test Plan / Test Cases）

> 版本：v2.0 ｜ 适用产品：Intel XPU SmarTune 1.5
> 维护者：QA / 研发
> 说明：SmarTune 不是纯监控面板，而是**以 root 权限实时修改系统状态的控制系统**（cgroups v2、CPU governor、iptables/tc、oom_score_adj、eBPF）。测试须覆盖"读"（监控展示）与"写"（调控生效 + 安全恢复）两侧。
>
> **本文档为可执行 Test Case 格式**：每条用例含「前置条件 / 测试步骤 / 预期结果」，步骤尽量给到具体命令与断言点。

---

## 1. 概述

### 1.1 被测系统组成
| 层 | 技术栈 | 关键路径 |
|---|---|---|
| 前端 | React 18 + TS + Ant Design + Vite | `balancer/dashboard/` |
| 后端 | Flask (WSGI)，HTTPS :9001，SSE + 2s 轮询 | `balancer/BalanceService.py` |
| 控制核心 | DynamicBalancer + controllers | `balancer/balancer/`, `balancer/controller/` |
| 监控采集 | PSI / eBPF(BCC) / /proc / sysfs / RAPL / PMT | `balancer/monitor/` |
| 配置 | YAML | `balancer/config/config.yaml`（加载器 `config/config.py`） |
| 持久化 | SQLite (Peewee ORM) | `balancer/db/` |

### 1.2 6 个 UI 页签
System Overview、App Resources、Processes、History、Balancer、About。

### 1.3 配置生效方式（写测试步骤时的重要依据）
| 生效方式 | 涉及配置 | 验证步骤要点 |
|---|---|---|
| **API 热更新 + 回写 YAML** | `weights_top`、`thresholds`(经 update_config_section)、`limit_policy`、`passive_resource_control`、`controlled_apps`（向导增删） | 改完立即验证行为，并 `cat config.yaml` 确认已回写 |
| **DB 存储（非 YAML）** | history `retention_days` | 通过 `/monitor/history/retention` 验证 |
| **手改 YAML 后需重启** | `network_*`、`blacklist`、`cooldown_time`、`*_busy_threshold`、`disk_*`、`app_priority`、`cgroup_mount`、`vendor`、`regular_update_sys_pressure_time`、`monitor_idle_check_interval` | 改 YAML → 重启服务 → 验证行为变化 |

---

## 2. 测试环境与前置条件

### 2.1 目标平台
硬件 MTL / PTL / WideCat Lake；OS Ubuntu / Debian，Python 3.12；内核支持 cgroups v2、PSI、eBPF/BCC、i915 PMU、Intel NPU PMT；依赖 `bcc`、`cpupower`。

### 2.2 环境矩阵（兼容性/降级）
| 维度 | 取值 |
|---|---|
| 加速器 | iGPU+NPU / 仅 iGPU / 无 XPU |
| 磁盘 | nvme / sata / 多盘 |
| 网卡 | 单网卡 / 多网卡 / 配置的 interface 不存在 |
| 内核 | 启用 PSI / 未启用 ｜ cgroups v2 / v1 |
| 依赖缺失 | 无 BCC ｜ 无 cpupower ｜ 无 root |

### 2.3 通用测试夹具（Fixtures）
- **后端启动**：root 启动，证书就位，`curl -k https://localhost:9001/...` 可访问。
- **鉴权 token**：记为 `$TOKEN`，登录后用于后续请求。
- **测试负载**：`stress-ng`（CPU/内存）、`fio`（磁盘）、`iperf3`（网络，配置内置 `testing_network_app: iperf`）。
- **对照工具**：`top/mpstat`、`free`、`iostat`、`intel_gpu_top`/`xpu-smi`、`tc -s qdisc`、`iptables -L -t mangle`、`cat /sys/fs/cgroup/<scope>/{cpu.max,memory.high,io.max}`、`cat /proc/<pid>/oom_score_adj`、`cpupower frequency-info`。
- **测试 App**：准备一个可长时间运行、可 stress 的样例进程（如自建 `stress-app.service` systemd scope），用于纳管与限额。

### 2.4 缺陷分级
| 级别 | 定义 |
|---|---|
| S1 致命 | 系统卡死/误限系统进程/残留规则致机器不可用/监控数据严重失真/安全漏洞 |
| S2 严重 | 核心调控不生效或不恢复；崩溃；数据错误 |
| S3 一般 | 局部功能异常、错误提示缺失、体验问题 |
| S4 轻微 | 文案/样式/低频小问题 |

### 2.5 优先级
P0＝调控正确性/监控准确性/安全恢复（每轮必测）；P1＝功能/API/安全/配置/兼容性；P2＝性能/稳定性/持久化；保留＝关键单测。
**判定铁律**：涉及"写系统"的用例，必须核对真实系统状态（cgroup/tc/governor/oom 实际值 + 实测效果），不能只看 `retcode:0`。

---

## 3. 功能测试（Functional）— P1

### TC-FN-OV-01　压力总览三表显示　【System Overview】
- **前置**：后端运行，系统空闲。
- **步骤**：
  1. 浏览器打开 UI，进入 System Overview。
  2. 观察 Disk IO / System / Network 三个压力表盘。
  3. 用 `fio` 制造磁盘压力，观察 Disk IO 表盘变化。
- **预期**：三表显示 0–100% 数值与 LOW/MEDIUM/HIGH/CRITICAL 等级；颜色与等级一致；施压后 Disk IO 表读数与等级随之上升。

### TC-FN-OV-02　资源卡片渲染
- **前置**：后端运行。
- **步骤**：进入 System Overview，逐一检查 CPU、Memory、Disk、Network、NPU、iGPU 六张卡片。
- **预期**：数值、单位、趋势曲线均正确渲染；无 `NaN`/`undefined`/空白。

### TC-FN-OV-03　刷新间隔切换
- **前置**：打开浏览器开发者工具 Network 面板。
- **步骤**：分别选择刷新间隔 2s / 3s / 5s，观察 `/monitor/dynamic_info` 请求间隔。
- **预期**：请求间隔与所选值一致。

### TC-FN-OV-04　时间窗切换
- **步骤**：切换 Trend / Last 1 min / Last 5 min。
- **预期**：曲线横轴时间窗随之变化，数据不错位。

### TC-FN-OV-05　关闭 Refresh
- **步骤**：关闭 Refresh 开关，观察 Network 面板与页面数据。
- **预期**：停止发起轮询请求，页面数据冻结在最后一帧。

### TC-FN-AR-01　Top-N 应用资源榜
- **前置**：运行 2–3 个已知资源占用的进程。
- **步骤**：进入 App Resources，对照 `curl -k "https://localhost:9001/monitor/app_resource_stats?n=10"`。
- **预期**：UI 列表与接口返回一致；按 score 降序排列。

### TC-FN-AR-02　调整排名权重后排序变化
- **前置**：记录当前榜单顺序。
- **步骤**：在设置中把 GPU 权重调高（或经 `/monitor/config/weights_top`），刷新榜单。
- **预期**：GPU 占用高的应用排名上升；磁盘 I/O 榜仍按纯吞吐排序（I/O 权重不可配）。

### TC-FN-PR-01　进程列表
- **步骤**：进入 Processes，对照 `top -b -n1`。
- **预期**：按 CPU 降序；PID/名称/用户/CPU%/内存 字段完整；数量级与 top 一致。

### TC-FN-PR-02　大量进程渲染
- **前置**：系统进程数 > 1000（可用脚本批量拉起 sleep）。
- **步骤**：打开 Processes 页，滚动列表。
- **预期**：渲染无卡顿、无截断错误、无崩溃。

### TC-FN-HS-01　历史时间范围筛选
- **前置**：服务已运行并积累历史快照。
- **步骤**：在 History 选择不同时间范围；对照 `curl -k "https://localhost:9001/monitor/history?range_seconds=300"`。
- **预期**：曲线数据点落在所选范围内，与接口 count 一致。

### TC-FN-HS-03　空历史友好提示
- **前置**：全新启动、无历史数据。
- **步骤**：打开 History。
- **预期**：显示空态提示，不报错、不白屏。

### TC-FN-BL-01　受控应用列表加载
- **步骤**：进入 Balancer，对照 `curl -k -X POST https://localhost:9001/app/get_controlled_app -d '{}'`。
- **预期**：展示每个 app 的优先级、OOM 分值、状态，与接口一致。

### TC-FN-BL-02　AddApp 向导全链路
- **前置**：存在一个可被关键词匹配的运行进程。
- **步骤**：
  1. 点击 Add App，输入关键词 → 触发 `discover_search`。
  2. 勾选候选进程 → 触发 `discover_extract`，检查自动填充的 bpf_name/process_names/commandline/id。
  3. 填写名称、优先级 → 提交 `new_controlled_app`。
- **预期**：三步顺畅；字段自动填充正确；提交后 app 出现在受控列表，且 `config.yaml` 的 `controlled_apps` 已追加该条目。

### TC-FN-BL-04　资源限额编辑（受上下界约束）
- **前置**：选定一个受控 app。
- **步骤**：
  1. 打开限额编辑，先取 `resource_limit_profile`（默认值 + min/max）。
  2. 输入合法值提交 → `resource_limit`。
  3. 尝试输入超上界/负值提交。
- **预期**：合法值成功；非法值被前端拦截或后端返回 101/102；表单滑块受 min/max 约束。

### TC-FN-BL-05　恢复/软移除/硬删除
- **步骤**：对同一 app 依次执行 restore、remove_from_control、purge_controlled_app。
- **预期**：restore 后限额清除；remove 仅 `controlled=false`，仍在 config 可再启用；purge 后 config + DB + BPF 三处均删除干净。

### TC-FN-BL-07　passive_control 开关
- **前置**：制造系统高压力使自动限额本会触发。
- **步骤**：
  1. 关闭 passive_control（`/monitor/config/passive_control` enabled=false）。
  2. 施加 CPU 压力至 high。
  3. 观察是否对 top app 自动限额。
  4. 手动下发一次限额、并保持网络控制开启。
- **预期**：关闭后**不再**自动限额/恢复；但手动限额与网络控制仍生效。

### TC-FN-AB-01　About 静态信息
- **步骤**：进入 About，对照 `dmidecode`、`uname -a`、`lscpu`、`free -h`。
- **预期**：BIOS/OS/内核/CPU/内存/GPU/NPU/驱动/固件 与系统实际一致。

### 3.x 错误处理（负面用例）

### TC-FN-ERR-01　后端断连
- **步骤**：UI 打开状态下 `systemctl stop`（或 kill）后端；观察 UI；再启动后端。
- **预期**：UI 显示明确错误态（非白屏）；后端恢复后自动重连、数据恢复刷新。

### TC-FN-ERR-02　SSE 断开重连
- **步骤**：断开网络/重启后端，观察 `/app/events` 连接。
- **预期**：SSE 自动重连；不重复堆积历史事件。

### TC-FN-ERR-03　非法限额提交
- **步骤**：`resource_limit` 提交负数 / 超上界 / 非数字。
- **预期**：返回 101 ARGUMENT_ERROR 或 102 DATA_ERROR；后端不写入坏值到 cgroup。

### TC-FN-ERR-04　AddApp 冲突
- **步骤**：`new_controlled_app` 提交已存在的 id / name / 进程重叠项。
- **预期**：返回 409 CONFLICT，`data.conflict` 指明冲突类型，提示可 purge 后重加。

### TC-FN-ERR-05　对不存在 app 操作
- **步骤**：对不存在的 app_id 执行 limit / restore / cancel_relaunch。
- **预期**：返回 404 NOT_EXISTING 或 103 OPERATING_ERROR，不崩溃。

### TC-FN-ERR-07　缺失数据源展示
- **前置**：无 NPU 或无 iGPU 的平台。
- **步骤**：查看 System Overview 对应卡片。
- **预期**：明确标注"不支持/不可用"，而非显示误导性的 `0.0%`。

---

## 4. 调控正确性测试（Control Effectiveness）— **P0 核心**

> 每条均需核对 **cgroup/tc/governor/oom 真实值** 与 **实测效果**，并验证**恢复**。

### TC-CT-CPU-01　按优先级下发 CPU 限额并核对 cpu.max
- **前置**：受控 app `app_id=X`，其 cgroup/scope 已知（记 `$SCOPE`）；策略 high=0.7。
- **步骤**：
  1. 设优先级 high：`curl -k -X POST .../app/set_priority -d '{"app_id":"X","priority":80}'`
  2. 下发限额：`curl -k -X POST .../app/resource_limit -d '{"app_id":"X","app_name":"...","priority":"high"}'`
  3. 读取：`cat /sys/fs/cgroup/$SCOPE/cpu.max`
- **预期**：`cpu.max` 的 quota/period ≈ 0.7 × 核数（按实现的周期基准换算），与策略一致。

### TC-CT-CPU-02　CPU 限额实测生效
- **前置**：接 TC-CT-CPU-01。
- **步骤**：
  1. 在该 app 内跑 `stress-ng --cpu <N> --timeout 60s`。
  2. 用 `top -p <pids>` / `mpstat` 观察实际占用。
- **预期**：实际 CPU 占用被压到配额附近（±容差），不超配额。

### TC-CT-CPU-04　CPU 限额恢复
- **步骤**：`curl -k -X POST .../app/resource_restore -d '{"app_id":"X"}'`；再 `cat cpu.max`。
- **预期**：`cpu.max` 恢复为 `max`；重新压测占用可回升。

### TC-CT-CPU-05　critical 应用不被限
- **步骤**：设 app 为 critical，触发限额流程，读 `cpu.max`。
- **预期**：critical 无 CPU 限额比例（策略未配），`cpu.max` 保持 `max`。

### TC-CT-MEM-01　memory.high 按比例
- **前置**：app 优先级 high（策略 mem high=0.3）。
- **步骤**：下发限额后 `cat /sys/fs/cgroup/$SCOPE/memory.high`。
- **预期**：值 ≈ 0.3 × 系统内存（按实现基准）。

### TC-CT-MEM-02　内存回收实测
- **步骤**：app 内 `stress-ng --vm 1 --vm-bytes <大于 high>`，观察是否触发回收（`memory.high` 事件 / RSS 被压制）。
- **预期**：内存增长在接近 high 阈值时被抑制。

### TC-CT-IO-01　io.max 下发核对
- **前置**：app 优先级 low（write=20MB/s、read=30MB/s、write_iops=1200、read_iops=11000）。
- **步骤**：下发限额后 `cat /sys/fs/cgroup/$SCOPE/io.max`，确认对应盘的 major:minor。
- **预期**：rbps/wbps/riops/wiops 与 low 策略换算值一致。

### TC-CT-IO-02　磁盘限速实测
- **步骤**：app 内 `fio --rw=write --bs=1M --size=2G --name=t`，用 `iostat -x 1` 观察吞吐。
- **预期**：实测写吞吐 ≈ 20 MB/s（±容差）。

### TC-CT-IO-04　I/O 渐进恢复
- **步骤**：撤除磁盘压力后持续观察 `io.max`。
- **预期**：压力回落后限额逐步放开直至移除。

### TC-CT-NET-01　网络规则下发
- **前置**：`enable_network_control=true`，`network_interface` 正确。
- **步骤**：启用后 `tc qdisc show dev <iface>`、`tc class show dev <iface>`、`iptables -t mangle -L`。
- **预期**：存在 HTB qdisc/class 与四优先级（system/critical/high/low）分类及 mark 规则。

### TC-CT-NET-02　出向限速实测
- **前置**：内置 `testing_network_app: iperf`（critical，session-1879.scope）。
- **步骤**：`iperf3 -c <server>` 发流，观察带宽。
- **预期**：实测带宽落在该优先级配置区间（critical 500000–900000 kbit/s）。

### TC-CT-NET-04　系统端口豁免
- **步骤**：对端口 22/53/80/443/123 产生流量，观察是否被限。
- **预期**：系统端口流量不受带宽限制。

### TC-CT-NET-05　网络压力升至 critical 收紧
- **步骤**：持续打满带宽使 network pressure 达 critical。
- **预期**：依次收紧 low、high 类带宽上限；`tc class` 的 ceil 值下降。

### TC-CT-NET-06　网络规则清除
- **步骤**：停止网络控制 / 停服务后 `tc qdisc show`、`iptables -t mangle -L`。
- **预期**：HTB、IFB、iptables mark 规则**完全清除**，无残留导致网络异常。

### TC-CT-GOV-01/02　Governor 切换
- **步骤**：施压使分级达 high → `cpupower frequency-info`；撤压回 low → 再查。
- **预期**：高压切 performance（频率上升），低压回 powersave。

### TC-CT-GOV-03　无 cpupower 降级
- **前置**：卸载/屏蔽 cpupower。
- **步骤**：触发 governor 切换。
- **预期**：优雅失败并告警，不崩溃、不卡系统。

### TC-CT-OOM-01　OOM 保护值
- **步骤**：对 critical app 设 OOM 保护 → `cat /proc/<pid>/oom_score_adj`。
- **预期**：值为 -500（或配置值）。

### TC-CT-OOM-03　OOM 恢复
- **步骤**：`remove_from_control` 后再读 `oom_score_adj`。
- **预期**：恢复为原始值。

### TC-CT-LOOP-01　压力驱动自动限额（闭环上行）
- **前置**：passive_control=true，存在受控 top app。
- **步骤**：`stress-ng` 施加 CPU/内存/IO 压力，使 PSI 分级升至 high/critical。
- **预期**：balancer 自动对 top app 施加限额（cgroup 值变化）并切 governor 为 performance。

### TC-CT-LOOP-02　cooldown 防抖
- **步骤**：在压力临界值附近波动，观察限额调整频率。
- **预期**：遵守 `cooldown_time`（默认 15s），不频繁抖动。

### TC-CT-LOOP-03　压力回落渐进恢复（闭环下行）
- **步骤**：停止施压，持续观察 cgroup 值与 governor。
- **预期**：配额逐步恢复至无限制，governor 回 powersave。

### TC-CT-PQ-01/02/03　优先级队列
- **前置**：制造 critical 压力或磁盘忙。
- **步骤**：
  1. 启动多个不同优先级的受控 app。
  2. 查询 `/app/get_pending_app`。
  3. 撤除压力，观察自动拉起顺序。
  4. 对某待启动项调用 `cancel_relaunch`。
- **预期**：压力期入队不拉起；恢复后按优先级 DESC 依次拉起；取消项状态变 stopped 并从队列移除。

### TC-CT-BPF-01/02　eBPF 启动/退出检测
- **前置**：BCC 可用；已注册含 bpf_name 的受控 app。
- **步骤**：启动该 app（execve）→ 观察 SSE；再退出该 app → 观察 SSE。
- **预期**：启动被实时检测并纳管、SSE 推 running；退出被检测、SSE 推状态、限额被清理。

### TC-CT-BPF-04　多进程/多 cgroup 聚合
- **前置**：`process_names` 含多个进程、分布在多 cgroup。
- **步骤**：查看该 app 的资源统计。
- **预期**：跨 cgroup 资源正确聚合，无遗漏、无重复计。

---

## 5. 安全与破坏性/恢复测试（Safety & Recovery）— **P0**

### TC-SF-01　后端 kill 后残留
- **前置**：已对某 app 施加限额。
- **步骤**：`kill -9 <backend_pid>`；检查 `/sys/fs/cgroup/$SCOPE/cpu.max` 等；重启后端。
- **预期**：重启后能识别/接管或清理，无孤儿 cgroup 累积。

### TC-SF-02　网络控制中崩溃残留
- **步骤**：启用网络控制后 kill 后端；`tc qdisc show`、`iptables -t mangle -L`；重启。
- **预期**：规则被清理或重启后重建；不残留导致断网。

### TC-SF-03　正常重启后接管
- **步骤**：`systemctl restart` 后端 → `/app/check_running_apps`。
- **预期**：已有受控 app 被重新识别，状态一致。

### TC-SF-04　停服务/卸载清理
- **步骤**：停服务后全面检查 cgroup 限额、tc、iptables、oom_score_adj、governor。
- **预期**：全部恢复默认，系统干净无残留。

### TC-SF-05　黑名单强约束（S1 重点）
- **前置**：`blacklist` 含 kworker/systemd/dbus/pipewire 等。
- **步骤**：
  1. 尝试通过向导 `discover_search` 搜索这些进程名。
  2. 尝试对其纳管/限额。
  3. 施加系统高压力，观察 balancer 是否会限制系统进程。
- **预期**：黑名单进程**绝不出现在候选**、**绝不被纳管或限制**。

### TC-SF-06　极端限额的下界保护
- **步骤**：尝试下发极小 CPU 配额（趋近 0）。
- **预期**：有下界保护，`cpu.max` 不为 0；系统不卡死。

### TC-SF-07　配置网卡不存在
- **前置**：`network_interface` 改为不存在的名字，重启。
- **步骤**：启用网络控制。
- **预期**：安全跳过并告警，不影响其它功能。

### TC-SF-09　限额/恢复反复压力（泄漏）
- **步骤**：脚本循环 limit→restore 1000 次；期间监控 cgroup 数量、后端句柄/内存。
- **预期**：无 cgroup/规则泄漏，无句柄/内存泄漏。

---

## 6. 监控数据准确性测试（Monitoring Accuracy）— **P0**

> 与权威工具对照，容差事先约定。以下每条：**步骤＝并行采集 SmarTune 值与基准工具值并比对**。

| 编号 | 指标 | 前置 | 步骤 | 预期 |
|---|---|---|---|---|
| TC-AC-01 | CPU 总/分核、P/E/LPE | 施加可控 CPU 负载 | 同时取 `dynamic_info.cpu` 与 `mpstat -P ALL 1` | 数量级与趋势一致，误差 ≤ 约定容差 |
| TC-AC-02 | 内存/swap | 跑内存负载 | 对照 `free -m`、`/proc/meminfo` | 一致 |
| TC-AC-03 | 磁盘吞吐/IOPS/iowait/util | `fio` 施压 | 对照 `iostat -x 1` | 一致 |
| TC-AC-04 | 网络 tx/rx | `iperf3` 发流 | 对照 `/sys/class/net/*/statistics`、`ifstat` | 一致 |
| TC-AC-05 | iGPU 引擎/频率/功耗/VRAM | GPU 负载 | 对照 `intel_gpu_top`/`xpu-smi` | 一致 |
| TC-AC-06 | NPU 利用率/功耗/温度/频率 | NPU 负载 | 对照 PMT/`xpu-smi`（MTL/ARL/LNL/PTL） | 一致 |
| TC-AC-07 | 每应用 CPU/内存/GPU/NPU 显存 | 已知负载进程 | 核对 `app_resource_stats` 归属 | 归属正确、无串号 |
| TC-AC-08 | PSI 分数与四级分级 | 逐级施压 | 按 thresholds(0.4/0.6/0.8/1.0)+weights(cpu2/mem7/io1) 手算复核 | 分级边界正确 |
| TC-AC-09 | 排名 score | 多负载进程 | 按 weights_top(cpu2/mem7/gpu5) 复算 | 排序与分值正确 |
| TC-AC-10 | 空闲态 | 系统空闲 | 观察各指标 | 接近 0，无异常尖峰 |

---
</content>

## 7. 后端 API 系统测试（API System Test）— P1

> 覆盖全部暴露端点。每端点验证：正常返回、参数缺失/非法、鉴权、并发冲突、响应契约（retcode/retmsg/data）。

### TC-API-AUTH-01　登录
- **步骤**：`curl -k -X POST https://localhost:9001/auth/login -H 'Content-Type: application/json' -d '{"pwd":"<正确token>"}'`
- **预期**：`retcode=0`，`data.authenticated=true`。

### TC-API-AUTH-02　错误 token
- **步骤**：同上传入错误 token。
- **预期**：`data.authenticated=false`；响应不泄露"用户存在性"等信息。

### TC-API-AUTH-03　未鉴权访问
- **步骤**：未登录直接调用受保护端点。
- **预期**：返回 401 UNAUTHORIZED / 109。

### TC-API-APP-01　App 端点 happy path（批量）
- **前置**：准备一个受控 app。
- **步骤**：依次调用 `get_apps`、`set_priority`、`get_priority_data`、`set_to_control`、`get_controlled_app`、`get_pending_app`、`set_oom_score`、`resource_limit_profile`、`resource_limit`、`resource_restore`、`remove_from_control`、`purge_controlled_app`，逐个检查返回。
- **预期**：全部 `retcode=0`；`data` 结构符合 `docs/API_ENDPOINTS.md` 契约。

### TC-API-APP-02　缺必填参数
- **步骤**：`set_priority` 缺 `app_id` 或 `priority`；`resource_limit` 缺 `priority`。
- **预期**：返回 101 ARGUMENT_ERROR。

### TC-API-APP-03　get_priority_data 参数组合
- **步骤**：分别仅传 `app_name`、仅传 `app_id`、都不传。
- **预期**：前两成功；都不传返回参数错误。

### TC-API-APP-04　new_controlled_app 冲突
- **步骤**：提交重复 id / name / 进程重叠。
- **预期**：409，`data.conflict` 指明 `id`/`name`/进程重叠。

### TC-API-APP-06　discover_extract 非法 pid
- **步骤**：传入不存在的 pid、系统关键 pid、负数。
- **预期**：优雅处理，不崩溃，不越权。

### TC-API-APP-08　SSE 契约
- **步骤**：`curl -k -N https://localhost:9001/app/events`，保持连接 ≥ 60s；期间触发一次 app 状态变化。
- **预期**：首帧 `{"type":"connected"}`；空闲每 30s 有 `: heartbeat`；状态变化推送含 `app_id/app_name/status/purpose`。

### TC-API-MON-01　app_resource_stats n 边界
- **步骤**：`?n=0`、`?n=-1`、`?n=100000`、`?n=abc`。
- **预期**：合理裁剪/默认值，不崩溃。

### TC-API-MON-02　history 参数组合
- **步骤**：组合 `snapshot_type`(static/dynamic/all)、`limit`(0/1/20000/20001)、`start_time`+`end_time`、`range_seconds`。
- **预期**：过滤正确；limit 越界被夹取到 1–20000；无 500。

### TC-API-MON-03　retention 越界
- **步骤**：POST `retention_days` = 0 / 8 / -1 / 3.5 / "abc"。
- **预期**：拒绝，仅接受 1–7 整数。

### TC-API-MON-04　weights_top 非法
- **步骤**：POST 负权重 / 非整。
- **预期**：拒绝（非负约束）。

### TC-API-MON-05　乐观并发冲突（重点）
- **前置**：GET 取得 `updated_at`。
- **步骤**：
  1. 客户端 A 用旧 `expected_updated_at` POST retention/weights_top/passive_control。
  2. 之前先由客户端 B 成功改一次（使 `updated_at` 变化）。
- **预期**：A 收到 409 CONFLICT，`data.current` 返回服务端当前值。

### TC-API-CT-01　畸形请求健壮性
- **步骤**：对全部 POST 端点发送畸形 JSON、错误 Content-Type、超大 body。
- **预期**：稳定返回错误码，不 500 崩溃、不泄露堆栈。

### TC-API-CT-02　错误 HTTP 方法
- **步骤**：对 GET-only 端点用 POST，反之亦然。
- **预期**：405 或规范错误返回。

---

## 8. 配置项覆盖测试（config.yaml Coverage）— P1

> `config.yaml` 是**用户可直接修改**的部分，直接影响调控行为，必须逐项覆盖。测试须区分三种生效方式（见 §1.3）：**热更新回写 / DB / 改后重启**。
> 通用步骤模板：**① 记录基线行为 → ② 按生效方式修改配置 → ③ 触发相关逻辑 → ④ 验证行为随配置变化 → ⑤（热更新项）`cat config.yaml` 确认回写、（重启项）确认重启后生效。**

### 8.1 压力阈值与权重

### TC-CFG-THR-01　thresholds 分级边界
- **配置**：`thresholds`(low/medium/high/critical)。**生效**：经 update_config_section 可热更新；手改需重启。
- **步骤**：把 `high` 从 0.8 改小（如 0.5）→（重启或热更）→ 施加中等压力 → 看 `dynamic_info.pressure.level`。
- **预期**：更低压力即被判为 high；分级边界随配置移动。

### TC-CFG-WT-01　weights（压力合成权重 cpu2/mem7/io1）
- **配置**：`weights`。**生效**：改后重启。
- **步骤**：把 `memory` 权重调低、`cpu` 调高，重启；分别单独施加内存压力与 CPU 压力，比较合成 score 变化。
- **预期**：合成压力分数对 CPU 更敏感、对内存更不敏感。

### TC-CFG-WTOP-01　weights_top（排名 cpu2/mem7/gpu5）
- **配置**：`weights_top`。**生效**：`/monitor/config/weights_top` 热更新回写。
- **步骤**：POST 提高 gpu 权重 → 立即看 App Resources 榜 → `cat config.yaml` 看 `weights_top.gpu`。
- **预期**：GPU 重的 app 排名上升；YAML 已回写；I/O 榜不受影响（纯吞吐）。

### 8.2 限额策略 limit_policy

### TC-CFG-LP-01　CPU rate 比例
- **配置**：`limit_policy.cpu.rate.{high,medium,low,undefined}`。**生效**：可经 update_limit_policy_for_priority 回写；手改需重启。
- **步骤**：把 `low` 从 0.4 改为 0.2 →（重启/热更）→ 对 low 优先级 app 下发限额 → `cat cpu.max`。
- **预期**：`cpu.max` 配额按新比例 0.2 换算。

### TC-CFG-LP-02　memory rate 比例
- **步骤**：同上改 `limit_policy.memory.rate.high` → 验证 `memory.high` 值随之变化。
- **预期**：一致。

### TC-CFG-LP-03　disk_io rate（read/write/iops）
- **步骤**：改 `limit_policy.disk_io.rate.low.write`（如 20→10）→ 重启 → 下发 low 限额 → `cat io.max` + `fio` 实测。
- **预期**：`io.max` wbps 与实测写吞吐随新值变化。

### TC-CFG-LP-04　policy 模式 combined vs separated
- **配置**：`limit_policy.policy`。**生效**：重启。
- **步骤**：切换 combined / separated，分别只施加 CPU 压力或只施加磁盘压力，观察是否联动限额。
- **预期**：combined 下 cpu/mem/disk 同条件联动；separated 下各自独立评估。

### TC-CFG-LP-05　资源开关 enabled
- **步骤**：把 `limit_policy.cpu.enabled` 设为 false，重启，触发限额。
- **预期**：CPU 不被限，其它资源仍按配置限。

### TC-CFG-DAF-01　dominant_app_reduce_factor
- **配置**：`dominant_app_reduce_factor`（默认 3.5）。**生效**：重启。
- **步骤**：构造"被限主导 app 且系统不忙"场景，改因子值前后对比权重/调整力度。
- **预期**：权重与调整因子按该值缩减，行为符合策略。

### 8.3 忙碌阈值

### TC-CFG-BUSY-01　cpu/memory/disk busy 阈值
- **配置**：`cpu_busy_threshold`、`memory_busy_threshold`、`disk_utilization_threshold`、`disk_iowait_threshold`、`disk_io_throughput_threshold_kb`。**生效**：重启。
- **步骤**：逐项调低阈值 → 重启 → 施加低于原阈值的负载 → 观察是否更早判"忙"并触发相应控制。
- **预期**：忙碌判定点随阈值移动。

### 8.4 app_priority 映射

### TC-CFG-PRIO-01　优先级数值映射
- **配置**：`app_priority`(critical100/high80/medium50/low20)。**生效**：重启。
- **步骤**：修改某级数值 → 重启 → 查询 `get_pending_app` 的 `priority_value` 与队列排序。
- **预期**：队列排序与数值映射一致。

### 8.5 网络控制配置（改后需重启）

### TC-CFG-NET-01　enable_network_control 开关
- **步骤**：设 false → 重启 → `tc qdisc show`。
- **预期**：不下发任何 tc/iptables 网络规则。

### TC-CFG-NET-02　network_interface
- **步骤**：改为真实的另一网卡 → 重启 → 检查规则挂载的接口。
- **预期**：规则挂在新接口上。（不存在的接口见 TC-SF-07）

### TC-CFG-NET-03　network_bandwidth_kbit 总带宽
- **步骤**：调整总带宽 → 重启 → iperf3 实测上限。
- **预期**：总带宽上限随配置变化。

### TC-CFG-NET-04　config_network_bw 各优先级区间
- **步骤**：修改 `high.max` → 重启 → 对 high 类 app 打流。
- **预期**：该优先级带宽上限随之变化。

### TC-CFG-NET-05　network_thresholds
- **步骤**：调低 critical 阈值 → 重启 → 打流。
- **预期**：更低网络压力即触发带宽收紧。

### TC-CFG-NET-06　network_system_ports 豁免
- **步骤**：向列表新增一个端口（如 8080）→ 重启 → 该端口打流。
- **预期**：新增端口流量被豁免不限速。

### TC-CFG-NET-07　network_burst_map
- **步骤**：修改各优先级 burst 值 → 重启 → 检查 `tc class` 的 burst。
- **预期**：burst 随配置变化。

### 8.6 黑名单 blacklist（改后需重启）

### TC-CFG-BL-01　新增黑名单项
- **步骤**：向 `blacklist` 增加一个测试进程名 → 重启 → 该进程运行时执行 `discover_search` 与 top 采样。
- **预期**：该进程从向导候选与监控采样中被隐藏，且不被纳管/限制。

### 8.7 时间/间隔类

### TC-CFG-TIME-01　cooldown_time
- **步骤**：改 `cooldown_time`（如 15→60）→ 重启 → 在压力临界波动。
- **预期**：限额调整最小间隔随之变化（对照 TC-CT-LOOP-02）。

### TC-CFG-TIME-02　采集/空闲检查间隔
- **配置**：`regular_update_sys_pressure_time`、`monitor_idle_check_interval`。**生效**：重启。
- **步骤**：调整后重启，观察压力刷新频率 / 空闲检查频率。
- **预期**：频率随配置变化。

### 8.8 passive_resource_control（热更新）

### TC-CFG-PASV-01
- 见 TC-FN-BL-07：`/monitor/config/passive_control` 热更新回写并即时生效。

### 8.9 controlled_apps（向导增删，回写 YAML）

### TC-CFG-APP-01　五字段解析
- **步骤**：手动在 `controlled_apps` 加一条含 name/id/commandline/bpf_name/process_names 的条目 → 重启 → 启动匹配进程。
- **预期**：balancer 按各字段识别、监控、聚合、显示名正确。（向导路径见 TC-FN-BL-02）

### 8.10 配置健壮性（负面）

### TC-CFG-ROB-01　非法/缺失配置
- **步骤**：制造非法 YAML（类型错误、缺关键 section、越界值、语法错误）→ 启动服务。
- **预期**：启动时校验并明确报错拒绝，或安全回退默认值，**不带坏值静默运行**。

### TC-CFG-ROB-02　回写不破坏文件
- **步骤**：多次经 API 热更新（weights_top/limit_policy）后 `cat config.yaml`。
- **预期**：仅目标行被改，**注释与缩进/其它内容保持不变**（config.py 的 patcher 声称保留注释）。

---

## 9. 安全测试（Security）— P1

### TC-SEC-01　命令注入（重点）
- **前置**：后端调用 cpupower/iptables/tc/pgrep 传入用户可控字段。
- **步骤**：向 `discover_search.keywords`、`new_controlled_app` 的 name/id/commandline/bpf_name/process_names 注入 shell 元字符：`; | & $() 反引号 换行 空格 --flag`；提交后观察系统与日志。
- **预期**：参数被安全转义/参数化，不执行注入命令，无非预期文件/进程/规则产生。

### TC-SEC-02　路径穿越
- **步骤**：`commandline`/`cgroup` 传 `../../` 或指向敏感 cgroup 路径。
- **预期**：校验/限定作用域，不操作非预期 cgroup。

### TC-SEC-03　pid 越权
- **步骤**：`discover_extract.pids` 传系统关键进程/非本 app pid。
- **预期**：不越权读取/操作。

### TC-SEC-04　鉴权与弱哈希
- **步骤**：审查 token 校验（SHA256 是否加盐）、会话管理、暴力尝试是否限速。
- **预期**：记录风险；无绕过；建议加盐/限速。

### TC-SEC-06　CORS `*` + CSRF
- **步骤**：从跨站页面向控制端点发请求（CORS 为 `*`）。
- **预期**：评估本地服务开放 `*` 的风险，验证是否需同源/自定义 Token 头防护。

### TC-SEC-07　TLS 配置
- **步骤**：`sslscan`/`nmap --script ssl-enum-ciphers -p 9001 localhost`。
- **预期**：现代 TLS，无弱套件。

### TC-SEC-08　信息泄露
- **步骤**：触发各类错误，检查响应/日志。
- **预期**：不含堆栈、绝对路径、token 等敏感信息。

### TC-SEC-09　资源耗尽
- **步骤**：海量并发 SSE 连接 / 超大 keywords / 高频请求。
- **预期**：有上限或限速，不被单点打挂。

### TC-SEC-10　静态与依赖扫描
- **步骤**：运行 coverity（已存在 `coverity_build/`）与 trivy 依赖扫描。
- **预期**：高危项清零。

---

## 10. 兼容性 / 降级测试（Compatibility）— P1

| 编号 | 场景 | 步骤 | 预期 |
|---|---|---|---|
| TC-CP-01 | MTL/PTL/WideCat Lake | 三平台各跑一轮功能+调控冒烟 | 采集与控制均正常 |
| TC-CP-02 | 无 NPU | 查看 NPU 卡片与 static_info | 标"不支持"，非误显 0% |
| TC-CP-03 | 无 iGPU | 同上查 GPU | 同上 |
| TC-CP-04 | 内核未启用 PSI | 缺 `/proc/pressure/*` 时启动 | 压力功能优雅降级 + 提示 |
| TC-CP-05 | cgroups v1/无 v2 | 在 v1 环境启动 | 控制功能安全禁用 + 提示 |
| TC-CP-06 | 无 BCC | 卸载 bcc 后启动 | eBPF 降级/禁用，不崩溃 |
| TC-CP-07 | 无 cpupower | 卸载后触发 governor | 功能禁用 + 告警 |
| TC-CP-08 | 多盘/多网卡 | 检查按设备区分 | 采集与限额按设备正确区分 |
| TC-CP-09 | 浏览器 | Chrome/Edge/Firefox | UI 与 SSE 均正常 |

---

## 11. 性能测试（Performance）— P2

### TC-PF-UI-01　页签切换流畅度
- **步骤**：用 DevTools Performance 录制在 6 个页签间切换。
- **预期**：切换 < 300ms，无明显卡顿/长任务。

### TC-PF-UI-02　大进程列表渲染
- **前置**：进程数 > 1000。
- **步骤**：打开 Processes，测首屏与滚动帧率。
- **预期**：首屏 < 1s，滚动流畅。

### TC-PF-BE-01　接口时延
- **步骤**：对监控端点做压测（如 `ab`/`wrk`/`hey`），统计 P50/P95。
- **预期**：在约定阈值内。

### TC-PF-BE-02　SmarTune 自身开销（关键）
- **步骤**：空闲态与"千级进程负载"下，用 `top -p <backend_pids>` / `pidstat` 观察后端 CPU/内存占用，持续 30 min。
- **预期**：自身占用低而稳定（降压工具不能反成负担）；无持续增长。

### TC-PF-BE-04　多客户端负载
- **步骤**：并发 N 个浏览器/脚本（SSE + 2s 轮询）。
- **预期**：后端负载平稳，响应不劣化。

---

## 12. 稳定性测试（Stability / Soak）— P2

### TC-ST-01　多 UI 并发改同一配置
- **步骤**：多个浏览器同时改 weights_top/retention/passive（携带各自 expected_updated_at）。
- **预期**：乐观并发正确，落败方得 409，最终一致，无脏写。

### TC-ST-02　多 UI 并发限额同一 app
- **步骤**：多客户端并发对同一 app limit/restore。
- **预期**：无竞态导致的错误 cgroup 状态。

### TC-ST-03　反复切换/开关（数小时）
- **步骤**：脚本驱动反复切页签、开关 Refresh，持续数小时；监控前端内存与后端连接数。
- **预期**：无内存增长、无 SSE 连接泄漏。

### TC-ST-04　长稳压力起伏（24–72h）
- **步骤**：长时间周期性施压/撤压，持续跑闭环。
- **预期**：闭环持续正确，无泄漏、无残留累积。

### TC-ST-06　高频 app 启停
- **步骤**：脚本高频启动/退出受控 app（触发大量 execve/exit）。
- **预期**：eBPF 事件不丢/不乱序导致状态错乱。

---

## 13. 数据持久化测试（Persistence）— P2

### TC-PS-01　保留期清理
- **步骤**：设 retention=1 天，注入超期快照（或调时钟）→ 触发清理 → 查 DB 行数与返回 `deleted`。
- **预期**：过期快照被真实删除，数据量受控。

### TC-PS-02　DB 体积有界
- **步骤**：长时间持续写快照，监控 SQLite 文件大小。
- **预期**：随保留期收敛，不无限增长。

### TC-PS-03　DB 损坏恢复
- **步骤**：模拟 DB 文件损坏/被占用后启动。
- **预期**：优雅处理，不崩溃，有明确日志。

### TC-PS-04　config+DB+BPF 三方一致性
- **步骤**：执行 new / remove / purge 后，分别核对 config.yaml、DB 记录、BPF 匹配缓存。
- **预期**：三处一致，无孤儿记录。

### TC-PS-05　升级迁移
- **步骤**：用旧版本 DB/config 升级到新版本启动。
- **预期**：schema/config 兼容迁移，旧数据可用。

---

## 14. 单元测试（保留的关键逻辑）

> 不追求覆盖率，但对"决策数学 + 配置校验"保留单测作回归网——纯逻辑算错会在 root 下写坏 cgroup，属 S1。

| 编号 | 目标 | 说明 |
|---|---|---|
| UT-01 | 配额计算 | 优先级比例 × 上下界 → cpu.max/memory.high/io.max；含边界(0、超界、undefined) |
| UT-02 | 压力分数与四级分级 | weights + thresholds 边界值 |
| UT-03 | 排名 score | weights_top 加权；I/O 纯吞吐 |
| UT-04 | 优先级队列排序/取消 | DESC 稳定序 |
| UT-05 | 配置解析与校验 | 缺字段/非法值/类型错误被拒（config.py from_file / update_* / patcher） |
| UT-06 | YAML patcher 保注释 | update_config_section / append_to_list_section / remove_from_list_section 不破坏注释与缩进 |
| UT-07 | 网络带宽区间/端口豁免映射 | 各优先级 min/max、system_ports |

---

## 15. 现有测试资产与建议

现有 `balancer/test/` 为**手工脚本**（`test_bservice.py`、`test_event.py`、`test_monitor_api.py`、`eBPF_event.py`、`psi_pressure.py` 等），非 pytest 组织；前端无单测。

建议：
1. 将 §7 API 系统测试与 §8 配置测试沉淀为 pytest + requests 自动化套件，纳入 CI。
2. §4/§5/§6（调控/恢复/准确性）建**受控测试床**（VM/容器 + stress-ng/fio/iperf3），脚本化断言 cgroup/tc/oom 真实值。
3. §14 单测用 pytest 维护，发布前必跑。
4. UI 关键链路（AddApp 向导、限额编辑、并发冲突提示）用 Playwright 做端到端冒烟。

---

## 16. 覆盖矩阵（速查）

| 领域 | 章节 | 优先级 |
|---|---|---|
| 功能 + 错误处理 | 3 | P1 |
| 调控正确性（闭环） | 4 | **P0** |
| 安全与破坏性恢复 | 5 | **P0** |
| 监控数据准确性 | 6 | **P0** |
| API 系统测试 | 7 | P1 |
| **配置项覆盖（config.yaml）** | **8** | **P1** |
| 安全测试 | 9 | P1 |
| 兼容性/降级 | 10 | P1 |
| 性能（含自身开销） | 11 | P2 |
| 稳定性/长稳 | 12 | P2 |
| 持久化 | 13 | P2 |
| 关键单元测试 | 14 | 保留 |
