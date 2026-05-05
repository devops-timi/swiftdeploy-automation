# policies/canary.rego
# Decides whether promoting to canary is safe based on current metrics.
# Thresholds come from data.json — never hardcoded here.

package canary

default allow := false

allow if {
    count(deny) == 0
}

# Block if error rate is too high
deny contains reason if {
    input.error_rate_percent > data.canary.max_error_rate_percent
    reason := sprintf(
        "Error rate %.2f%% exceeds maximum %.2f%% over last 30s",
        [input.error_rate_percent, data.canary.max_error_rate_percent]
    )
}

# Block if P99 latency is too high
deny contains reason if {
    input.p99_latency_ms > data.canary.max_p99_latency_ms
    reason := sprintf(
        "P99 latency %.0fms exceeds maximum %.dms over last 30s",
        [input.p99_latency_ms, data.canary.max_p99_latency_ms]
    )
}