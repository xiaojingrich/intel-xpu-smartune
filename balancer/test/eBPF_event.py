# Copyright (c) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

from bcc import BPF
import ctypes

# BPF constants (must match C definitions)
COMM_LEN = 32
PY_MAX_TARGET_LEN = 32
MAX_DYNAMIC_APPS = 32

bpf_code = """
#include <uapi/linux/ptrace.h>
#include <linux/sched.h>

#define COMM_LEN 32
#define MAX_TARGET_LEN 32
#define MAX_DYNAMIC_APPS 32

struct event_t {
    u32 pid;
    char comm[COMM_LEN];
    char filename[64];
    char blocked_type[16];  // "static" or "dynamic"
};

struct appname_t {
    char name[MAX_TARGET_LEN];
};

BPF_HASH(blocked_apps, u32, struct appname_t);
BPF_PERF_OUTPUT(events);

// Static blocklist
static const char INITIAL_TARGETS[][MAX_TARGET_LEN] = {
    "chrome", "chromium", "edge", "brave", "notepad"
};

static inline int is_substring(const char *str, const char *substr) {
    if (!str || !substr || substr[0] == '\\0') {
        return 0;
    }

    for (int j = 0; j < MAX_TARGET_LEN && str[j] != '\\0'; j++) {
        int k = 0;
        while (k < MAX_TARGET_LEN && substr[k] != '\\0' && str[j + k] == substr[k]) {
            k++;
        }
        if (k < MAX_TARGET_LEN && substr[k] == '\\0') {
            return 1;
        }
    }
    return 0;
}

static inline int bpf_strstr(const char *str, const char *substr) {
    if (!str || !substr || substr[0] == '\\0') return 0;

    for (int i = 0; i < COMM_LEN && str[i] != '\\0'; i++) {
        int match = 1;
        #pragma unroll
        for (int j = 0; j < MAX_TARGET_LEN; j++) {
            if (substr[j] == '\\0') break;
            if (str[i+j] == '\\0' || str[i+j] != substr[j]) {  
                match = 0;
                break;
            }
        }
        if (match && substr[0] != '\\0') {  
            return 1;
        }
    }
    return 0;
}

TRACEPOINT_PROBE(syscalls, sys_enter_execve) {
    const char **argv = (const char **)args->argv;
    char fname[64] = {0};
    char comm[COMM_LEN] = {0};

    bpf_get_current_comm(&comm, sizeof(comm));

    const char *fname_ptr = NULL;
    bpf_probe_read_user(&fname_ptr, sizeof(fname_ptr), &argv[0]);
    if (!fname_ptr || bpf_probe_read_user_str(fname, sizeof(fname), fname_ptr) < 0) {
        return 0;
    }

    // 1. Check static blocklist
    #pragma unroll
    for (int i = 0; i < sizeof(INITIAL_TARGETS)/sizeof(INITIAL_TARGETS[0]); i++) {
        if (is_substring(fname, INITIAL_TARGETS[i])) {
            struct event_t event = {};
            u64 pid_tgid = bpf_get_current_pid_tgid();
            event.pid = pid_tgid >> 32;
            bpf_probe_read_kernel_str(&event.comm, sizeof(event.comm), comm);
            bpf_probe_read_kernel_str(&event.filename, sizeof(event.filename), fname);
            bpf_probe_read_kernel_str(&event.blocked_type, sizeof(event.blocked_type), "static");

            bpf_trace_printk("BLOCKED(static): comm=%s\\n", comm);
            bpf_trace_printk("BLOCKED(static): path=%s\\n", fname);
            events.perf_submit(args, &event, sizeof(event));
            bpf_send_signal(9);
            return 0;
        }
    }

    // 2. Check dynamic blocklist    
    u32 key = 0;
    struct appname_t *val;
    int count = 0;

    while (count < MAX_DYNAMIC_APPS && (val = blocked_apps.lookup(&key))) {
        if (val) {
            if (bpf_strstr(fname, val->name)) {
                struct event_t event = {};
                u64 pid_tgid = bpf_get_current_pid_tgid();
                event.pid = pid_tgid >> 32;
                bpf_probe_read_kernel_str(&event.comm, sizeof(event.comm), comm);
                bpf_probe_read_kernel_str(&event.filename, sizeof(event.filename), fname);
                bpf_probe_read_kernel_str(&event.blocked_type, sizeof(event.blocked_type), "dynamic");

                bpf_trace_printk("BLOCKED(dynamic): comm=%s\\n", event.comm);
                bpf_trace_printk("BLOCKED(dynamic): path=%s\\n", event.filename);
                bpf_trace_printk("Submitting event: pid=%d\\n", event.pid);
                events.perf_submit(args, &event, sizeof(event));
                bpf_send_signal(9);
                return 0;
            }
        }
        key++;
        count++;
    }

    return 0;
}
"""

# Initialize BPF
bpf = BPF(text=bpf_code)

def print_event(cpu, event, size):
    event = bpf["events"].event(event)
    filename = event.filename.decode('utf-8', 'ignore')
    comm = event.comm.decode('utf-8', 'ignore')
    blocked_type = event.blocked_type.decode('utf-8', 'ignore')

    print(f"BLOCKED({blocked_type}): PID={event.pid}, COMM={comm}, FILENAME={filename}")

def add_to_blacklist(app_name):
    print(f"add_to_blacklist... '{app_name}'")

    class AppName(ctypes.Structure):
        _fields_ = [("name", ctypes.c_char * PY_MAX_TARGET_LEN)]

    value = AppName()

    # Null-terminate the name
    app_name_bytes = app_name.encode('utf-8')[:PY_MAX_TARGET_LEN - 1]
    value.name = app_name_bytes + b'\0'

    # Find next available key
    next_key = 0
    while next_key < MAX_DYNAMIC_APPS:
        try:
            _ = bpf["blocked_apps"][ctypes.c_uint32(next_key)]
            next_key += 1
        except KeyError:
            break

    if next_key >= MAX_DYNAMIC_APPS:
        print("Blacklist is full, cannot add more apps")
        return

    key = ctypes.c_uint32(next_key)

    print(f"Setting key={key.value}, value.name={value.name} (len={len(app_name_bytes)})")

    bpf["blocked_apps"][key] = value

    val = bpf["blocked_apps"][key]
    print(f"Verification: stored value={val.name.decode('utf-8', errors='replace')}")

    print(f"Added '{app_name}' to dynamic blacklist")

add_to_blacklist("firefox")
add_to_blacklist("wget")

print("Opening perf buffer...")
bpf["events"].open_perf_buffer(print_event)
print("Monitoring execve()... Ctrl+C to exit")

while True:
    try:
        bpf.perf_buffer_poll(timeout=100)
        # bpf.trace_print()
    except KeyboardInterrupt:
        break
    except Exception as e:
        print(f"Error: {e}")
        break