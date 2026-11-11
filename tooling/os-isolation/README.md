# Performance Isolation under Resource Contention: KVM VMs vs. LXC Containers on Proxmox VE

OS 課程期末專題（Fall 2026）的實驗程式。
Member A（平台與量測）· Member B（實驗與分析）

一句話：在同一台 Proxmox VE 主機上，讓一台 KVM VM 和一個 LXC container 當受測者（victim），逐步加入 0/1/2/4/8 個吵鬧鄰居（noisy neighbours），量 victim 的 p95 延遲和吞吐量退化多少，再用主機端的 kernel 計數器（cgroup v2 PSI、vCPU run-delay、guest steal）解釋退化從哪裡來。

```
config/experiment.toml   所有實驗參數（唯一的設定檔，凍結後改 config_version）
host/                    在 Proxmox 主機上以 root 執行          — Member A
  preflight.sh           INF-01  檢查平台、產生 docs/host-spec.md 與 docs/platform.json
  provision.sh           INF-02~04  建立 VM 201、CT 301、鄰居 401–408
  validate.py            INF-05/06  驗證量測鏈，產生 docs/validation.md
  run_one.py             RUN-01  單次實驗
  run_matrix.py          RUN-02~04  隨機區塊矩陣，可中斷續跑
  sampler.py             每秒讀取 cgroup / PSI / schedstat；也可單獨當即時監看工具
  pvelib.py  cfg.py  simulate.py   共用函式庫
guest/                   會被複製到 victim 裡的 /opt/bench/
  bench_cpu.sh           sysbench cpu → 一行 JSON
  bench_io.sh            fio 4k randread O_DIRECT → 一行 JSON
analysis/                不需要 Proxmox，任何有 Python 3.11 的電腦都能跑 — Member B
  load.py  report.py     ANA-01~05  表格、圖、summary.md
docs/                    preflight / provision / validate 自動產生的紀錄（報告附錄用）
  example-simulated/     模擬模式產生的 summary.md 與四張圖，讓你們先看輸出長什麼樣子（數字不能用）
results/<label>/         runs.csv、samples.csv、raw/*.json、analysis/
```

---

## §Platform：先決定在哪台機器上跑（請先讀這段）

`host/preflight.sh` 會把平台判定成三種之一，結果寫進 `docs/platform.json`，之後**每一筆** `runs.csv` 都會記錄這個判定：

| 判定 | 意思 | 程式的行為 |
|---|---|---|
| `BARE_METAL` | Proxmox 直接裝在實體機上 | 正常執行 |
| `NESTED_VMWARE`（或其他 `NESTED_*`） | Proxmox 本身是一台 VM | 可以跑，但 `run_matrix.py` 必須加 `--allow-nested`，報告裡要寫明限制 |
| `BLOCKED_NO_KVM` | Proxmox 裡沒有 `/dev/kvm` | VM 根本開不起來，所有腳本拒絕執行 |

### 為什麼 VMware Workstation 裡的 Proxmox 是個問題

這個專題比較的是「KVM VM」和「LXC container」的隔離。Proxmox 裝在 VMware 裡時，兩邊付出的代價不一樣：

- **LXC container** 只是 Proxmox kernel 上的一組 process，它只多了一層 VMware。
- **KVM VM** 變成「VM 裡的 VM」（nested virtualization）。每次 VM exit、每次 EPT miss、每個磁碟 I/O 都要先經過 KVM，再經過 VMware，最後才到 Windows。

所以量到「VM 比 CT 慢」時，分不出來是 KVM 本身的隔離比較差，還是 nested 的額外成本。CPU 密集的部分影響較小（vCPU 大多時間直接在硬體上跑），**磁碟 I/O 的比較基本上不能直接下結論**。另外，如果 Windows 開著 Hyper-V（WSL2、Docker Desktop、記憶體完整性都會開啟它），VMware 會改用 Hyper-V 的 API 執行，這時 VMware 無法把 VT-x 傳給 Proxmox，結果就是 `BLOCKED_NO_KVM`。

### 三個選項

| 選項 | 做法 | 優點 | 缺點 |
|---|---|---|---|
| **1. 實體機（建議）** | 桌機 i5-14400 用另一顆 SSD（或外接 SSD 開機）直接裝 Proxmox | 結果可以直接跟 Sharma 2016 比較；沒有任何額外限制要解釋 | 需要一顆空的 SSD；i5-14400 是 P/E 混合核心，preflight 會提示 |
| **2. 實驗室伺服器** | 請蘇育生老師的實驗室借一台機器裝 Proxmox | 同上，而且通常核心數多、記憶體大 | 要等借機器 |
| **3. 繼續用 VMware（nested）** | 依下面步驟讓 VMware 把 VT-x 傳進去，報告寫明限制 | 現在就能開始，demo 時帶筆電就好 | VM 這組的數字是 nested 的上界；I/O 比較不能下結論 |

選項 3 的 Windows 11 設定（每台要跑 Proxmox 的電腦都要做，做完要重開機）：

1. **設定 > 隱私權與安全性 > Windows 安全性 > 裝置安全性 > 核心隔離詳細資料**：把「記憶體完整性」關閉。
2. **控制台 > 程式集 > 開啟或關閉 Windows 功能**：取消勾選「Hyper-V」、「虛擬機器平台」、「Windows Hypervisor 平台」。
   注意：這會讓 WSL2 和 Docker Desktop 無法使用，之後要用再勾回來。
3. 重開機後，在 VMware 裡對 Proxmox 這台 VM（VMware 沒有繁體中文介面，以下是英文介面的名稱）：**Edit virtual machine settings > Processors**，勾選 **Virtualize Intel VT-x/EPT or AMD-V/RVI**。
4. 建議的 VMware VM 規格：處理器 8 核以上、記憶體 12 GB 以上、硬碟 150 GB 以上（**Store virtual disk as a single file**，有空間的話勾 **Allocate all disk space now** 效果更穩）、網路用 NAT。
5. 開機進 Proxmox 後執行 `bash host/preflight.sh`。判定應該是 `NESTED_VMWARE`；如果是 `BLOCKED_NO_KVM`，代表第 1、2 步還有東西開著（可在 Windows 用系統管理員執行 `bcdedit /set hypervisorlaunchtype off` 再重開機）。

不管選哪一個，程式碼都一樣，只有 `config/experiment.toml` 的網路設定要依機器調整。

---

## 快速開始（對照實作計畫的 ticket）

以下指令都在 **Proxmox 主機上以 root** 執行。需要 Proxmox VE 8 以上（內建 Python 3.11+）；實驗腳本只用標準函式庫，不用另外安裝套件。

```bash
# 0. 把整個資料夾複製到 Proxmox 主機（在自己的電腦上執行）
scp -r os-isolation root@<proxmox-ip>:/root/
ssh root@<proxmox-ip>
cd /root/os-isolation

# 1. INF-01 檢查平台 → docs/host-spec.md、docs/platform.json
bash host/preflight.sh

# 2. 依 preflight 印出的 bridge 網段，修改 config/experiment.toml 的 [host]：
#    vm_ip（選 DHCP 範圍外的位址）、cidr、gateway

# 3. INF-02~04 建立所有 guest（第一次約 20–40 分鐘，需要能連網下載 Ubuntu 映像檔）
bash host/provision.sh all

# 4. INF-05/06 驗證量測鏈 → docs/validation.md（約 1 分鐘，全部 PASS 才往下）
python3 host/validate.py

# 5. RUN-01 單次冒煙測試
python3 host/run_one.py --arm vm --dim cpu --n 2
python3 host/run_one.py --arm ct --dim io  --n 2

# 6. RUN-02 + GATE-1：只跑 n=0，每個組合 5 次，檢查重現性
python3 host/run_matrix.py --label baseline --neighbours 0        # NESTED 平台要加 --allow-nested
python3 analysis/report.py results/baseline                       # 看 summary.md 的 GATE-1 欄

# 7. RUN-03 先導掃描：每個條件跑 1 次
python3 host/run_matrix.py --label pilot --reps 1

# 8. CFG-01 凍結設定：把 config_version 改成 "1.0"，之後不再改參數

# 9. RUN-04 主矩陣（100 runs，約 3–4 小時；在 tmux 裡跑，SSH 斷線也不會中斷）
apt install -y tmux && tmux new -s exp
python3 host/run_matrix.py --label main
#   Ctrl-B D 離開 tmux；tmux attach -t exp 回來看進度
#   中斷後用同一個指令重跑，會跳過已經成功的 run

# 10. ANA-01~05 分析：把 results/ 複製回自己電腦（pip install -r analysis/requirements.txt），
#     或直接在主機上 apt install -y python3-pandas python3-scipy python3-matplotlib
python3 analysis/report.py results/main          # → results/main/analysis/summary.md 與圖
```

`run_matrix.py --dry-run` 會印出執行順序和預估時間，不會真的跑。

### 現場 demo 用的即時監看

```bash
python3 host/sampler.py --arm vm --seconds 60    # 每秒印出 victim 的 CPU 使用、PSI、vCPU 等待
```

---

## Run protocol：每一次 run 做了什麼

`run_one.py` 和 `run_matrix.py` 走同一個函式 `execute_run()`，步驟固定：

1. 確認兩個 victim 和所有鄰居都在執行中（不在就開機），並停掉所有鄰居上殘留的負載。
2. **冷卻**：等主機的 CPU 和 I/O PSI（`some avg10`）都低於 `cooldown_psi_avg10`，最多等 `cooldown_max_s` 秒；實際等了多久記在 `cooldown_wait_s`。
3. 清空主機和 VM 的 page cache（`drop_caches`）。
4. 在前 n 個鄰居上用 `pct exec … setsid -f` 背景啟動負載：CPU 維度用 `stress-ng --cpu <cores> --cpu-load <L>`，I/O 維度用 `fio randwrite 4k direct iodepth=<D>`。
5. **暖機** `warmup_s` 秒，然後確認真的有 n 個鄰居在跑（`nbr_active_start`），不對就判定失敗。
6. 找到 victim 的 cgroup（VM：`/var/run/qemu-server/<vmid>.pid` 對應的 `qemu.slice/<vmid>.scope`；CT：`lxc/<ctid>`）和 VM 的 vCPU thread（名稱為 `CPU n/KVM`）。
7. 啟動 sampler（每秒一次），在 victim 裡執行 benchmark `duration_s` 秒，結束後停 sampler；VM 另外記錄前後的 guest steal。
8. 再確認一次 n 個鄰居還在跑（`nbr_active_end`），全部符合才 `ok=1`。
9. 無論成功或失敗都停掉鄰居負載，並把結果寫進 `runs.csv`、`samples.csv`、`raw/<run_id>.json`。

`run_matrix.py` 把全部條件分成 `reps` 個區塊，每個區塊包含每個條件各一次，順序用固定 seed 隨機打亂，讓時間上的漂移（SSD 狀態、溫度）平均分散到所有條件。失敗的 run 會自動重試一次（`--retries`），失敗原因留在 `note` 欄。

## 輸出格式

- `runs.csv`：每個 run 一列。重要欄位：`platform`（平台判定）、`config_version`、`arm`、`dim`、`n`、`intensity`、`block`、`p95_ms`、`mean_ms`、`throughput`、`unit`、`nbr_active_start/end`、`guest_steal_ticks`、`cooldown_wait_s`、`ok`、`note`。
- `samples.csv`：長格式 `(run_id, t, target, metric, value)`，全部是**累計值**；分析時用頭尾相減除以時間得到速率。`target` 是 `victim`、`nbr1..nbrN`、`host`。
- `raw/<run_id>.json`：benchmark 的原始 JSON 和該列的完整內容。
- `results/<label>/experiment.toml`、`platform.json`：跑這批資料時的設定和平台，跟資料放在一起。

## 設計決策與跟計畫不同的地方

- **兩個 victim 同時開著**：計畫圖上一次只開一個 victim。實作上兩個都保持開機，但不在測量中的那個是閒置的。理由是開關機會改變主機狀態（page cache、記憶體配置），而且 VM 開機要時間；閒置的 guest 對 CPU 和磁碟幾乎沒有負擔，冷卻步驟也會確認主機是安靜的。報告的方法章節要寫這一點。
- **鄰居一直開著，負載用 `pct exec` 啟停**：避免每次 run 都要開關 8 個 container。
- **鄰居一律是 LXC（DEC-03）**，核數自動設為主機邏輯 CPU 數的 1/4（DEC-04），讓超賣比 ρ(n) 從小於 1 涵蓋到大於 2。
- **兩個 victim 在同一個 LVM-thin 儲存上（DEC-02）**，VM 磁碟 `cache=none`，benchmark 用 O_DIRECT。
- **CPU throttling 計數器會是 0**：Proxmox 預設沒有設 `cpu.max`，爭用會表現為排隊等待（PSI、run-delay），不是 throttling。這是預期中的結果，`summary.md` 會自動檢查並寫一句說明。

## 模擬模式（只用來測試程式）

```bash
EXP_SIMULATE=1 python3 host/validate.py
EXP_SIMULATE=1 python3 host/run_matrix.py --label simtest --reps 2
```

不需要 Proxmox：victim 是本機直接執行的 `guest/bench_*.sh`，鄰居是本機的 sysbench / fio process，cgroup 檔案是用主機 PSI 模擬出來的。結果的 `platform` 欄是 `SIMULATED`，圖上有浮水印，**絕對不能拿來報告**。可以用 `EXP_CONFIG=<另一個 toml>` 改用較短的 duration 來加快測試。

## 疑難排解

| 狀況 | 處理 |
|---|---|
| preflight 判定 `BLOCKED_NO_KVM` | 見 §Platform 選項 3 的 Windows 設定 |
| provision 卡在等 VM 的 SSH | `vm_ip` 必須在 bridge 的網段內、DHCP 範圍外；`gateway` 要對（VMware NAT 通常是 `x.x.x.2`）；可用 `qm terminal 201` 看 console |
| 找不到 CT template | 執行 `pveam update` 後再跑 `provision.sh ct` |
| validate 的 vCPU 檢查顯示 TCG | VM 在軟體模擬下執行，結果無效；修好 `/dev/kvm` 後 `qm set 201 --kvm 1` |
| GATE-1 沒過（CV 太大） | 依序檢查：其他 VM/CT 是否都關了、CPU governor 設 `performance`、Windows 端（若是 nested）關掉其他程式和省電模式、SSD 是否已預熱（先跑一輪 pilot） |
| 想重來 | `bash host/provision.sh destroy --yes` 會刪除本專題建立的所有 guest |
| 更新 guest 裡的 benchmark 腳本 | `bash host/provision.sh push` |
