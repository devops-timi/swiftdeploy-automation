# policies/infrastructure.rego
# Decides whether a deployment is safe based on host resources.
# Thresholds come from data.json — never hardcoded here.

package infrastructure

# "deny" is a set of reason strings.
# If deny is empty → deployment is allowed.
# If deny has any entries → deployment is blocked.

default allow := false

allow if {
    # Allow only when there are zero deny reasons
    count(deny) == 0
}

# Block if disk free space is below the minimum threshold
deny contains reason if {
    input.disk_free_gb < data.infrastructure.min_disk_free_gb
    reason := sprintf(
        "Disk free %.1fGB is below minimum %.1fGB",
        [input.disk_free_gb, data.infrastructure.min_disk_free_gb]
    )
}

# Block if CPU load average is too high
deny contains reason if {
    input.cpu_load > data.infrastructure.max_cpu_load
    reason := sprintf(
        "CPU load %.2f exceeds maximum %.2f",
        [input.cpu_load, data.infrastructure.max_cpu_load]
    )
}

# Block if memory usage is too high
deny contains reason if {
    input.mem_percent > data.infrastructure.max_mem_percent
    reason := sprintf(
        "Memory usage %.1f%% exceeds maximum %.1f%%",
        [input.mem_percent, data.infrastructure.max_mem_percent]
    )
}