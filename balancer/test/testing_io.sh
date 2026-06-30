#!/bin/bash

# Copyright (c) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

# Drop page cache
echo "Clean cache."
sync; echo 3 | sudo tee /proc/sys/vm/drop_caches

echo "Starting...."

BLOCK_SIZE="16M"       # Block size, adjust based on disk type
TOTAL_BLOCKS="100"     # Total data = 16M * 100 = 1600M (1.6GB)
TEST_FILE1="/tmp/testfile1"
TEST_FILE2="/tmp/testfile2"

# cgroup paths
CGROUP_PATH1="/user.slice/user-1000.slice/user@1000.service/app.slice/app-org.gnome.Terminal.slice/vte-spawn-4573f009-2887-47a4-a7d8-f573b6965109.scope"
CGROUP_PATH2="/user.slice/user-1000.slice/user@1000.service/app.slice/app-org.gnome.Terminal.slice/vte-spawn-c9baf8fb-e37b-417e-9934-4719167082dd.scope"

echo "Write:"
# Run tests in parallel
parallel -j2 --linebuffer <<EOF
cgexec -g io:${CGROUP_PATH2} dd if=/dev/zero of=${TEST_FILE2} bs=${BLOCK_SIZE} count=${TOTAL_BLOCKS} oflag=direct status=progress
cgexec -g io:${CGROUP_PATH1} dd if=/dev/zero of=${TEST_FILE1} bs=${BLOCK_SIZE} count=${TOTAL_BLOCKS} oflag=direct status=progress
EOF

# Drop page cache
echo "Clean cache."
sync; echo 3 | sudo tee /proc/sys/vm/drop_caches

echo "Read:"
# Run tests in parallel
parallel -j2 --linebuffer <<EOF
cgexec -g io:${CGROUP_PATH2} dd if=${TEST_FILE2} of=/dev/null bs=${BLOCK_SIZE} count=${TOTAL_BLOCKS} iflag=direct status=progress
cgexec -g io:${CGROUP_PATH1} dd if=${TEST_FILE1} of=/dev/null bs=${BLOCK_SIZE} count=${TOTAL_BLOCKS} iflag=direct status=progress
EOF

# Cleanup
echo "Testing done, clean tmp files."
rm /tmp/testfile1 /tmp/testfile2