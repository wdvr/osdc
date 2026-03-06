# TODO List - Post-Testing Tasks

## High Priority

- [x] **Auto get-ssh-config in `gpu-dev connect`** ✅ DONE
  - Added to PR #50 (commit c9d0c9a)
  - Added to test/all-fixes-consolidated (commit 54b81af)
  - Auto-downloads SSH config if missing
  - Shows helpful error on auth failure with exact commands

- [ ] **Debug /ccache_shared performance issues**
  - Check EFS mount options and performance mode
  - Review concurrent access patterns (50+ users)
  - Look for file locking or metadata bottlenecks
  - Check if provisioned throughput is sufficient

- [x] **Add EBS snapshot warm-up PR** ✅ ALREADY INCLUDED
  - PR #39 (commit 1c9f17f) - disk-warmer init container
  - Already in test/all-fixes-consolidated (lines 3704-3714 in Lambda)
  - Pre-warms metadata → critical dirs → remaining files

- [ ] **Merge profiling timings PR**
  - PR #42: feat/reservation-timing-trace
  - Adds `--trace` flag to show detailed reservation timing
  - NOT yet in test/all-fixes-consolidated - needs merge
  - Also includes: chown skip optimization (30-40s speedup)

## Testing

- [ ] **Test git clone with cache**
  - Create reservation
  - Run `git clone https://github.com/pytorch/pytorch`
  - Verify cache is used
  - Check speed improvement

- [ ] **Monitor EFA speed benchmark**
  - Waiting for agent to complete
  - Compare RDMA vs TCP bandwidth

## Documentation

- [x] Add-user tested and approved ✅

## Completed
- [x] All 7 PRs tested ✅
- [x] Git-cache fixed and re-enabled ✅
- [x] Add-user test setup ✅
