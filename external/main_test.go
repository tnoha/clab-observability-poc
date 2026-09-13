package main

import (
	"net/netip"
	"reflect"
	"testing"
	"time"
)

func waveformConfig(slot int) config {
	return config{
		base: 1000, amplitude: 300, period: 600 * time.Second, step: 30 * time.Second, phaseSlot: slot,
	}
}

func TestTargetCountBoundsAndPhase(t *testing.T) {
	epoch := time.Unix(0, 0).UTC()
	if got := targetCount(epoch, waveformConfig(0)); got != 1000 {
		t.Fatalf("start target = %d, want 1000", got)
	}
	if got := targetCount(epoch.Add(150*time.Second), waveformConfig(0)); got != 1300 {
		t.Fatalf("peak target = %d, want 1300", got)
	}
	if got := targetCount(epoch.Add(450*time.Second), waveformConfig(0)); got != 700 {
		t.Fatalf("trough target = %d, want 700", got)
	}
	if got := targetCount(epoch, waveformConfig(2)); got != 1300 {
		t.Fatalf("phase-shifted target = %d, want 1300", got)
	}
}

func TestEightPhasesKeepAggregateStable(t *testing.T) {
	epoch := time.Unix(0, 0).UTC()
	for seconds := 0; seconds < 600; seconds += 30 {
		total := 0
		for slot := range phaseCount {
			total += targetCount(epoch.Add(time.Duration(seconds)*time.Second), waveformConfig(slot))
		}
		if total < 7998 || total > 8002 {
			t.Fatalf("aggregate at %ds = %d, want approximately 8000", seconds, total)
		}
	}
}

func TestPrefixPoolProducesUniqueRoutes(t *testing.T) {
	pool := netip.MustParsePrefix("198.18.0.0/20")
	routes := prefixes(pool, 1300)
	if len(routes) != 1300 || routes[0].String() != "198.18.0.0/32" || routes[1299].String() != "198.18.5.19/32" {
		t.Fatalf("unexpected routes: first=%s last=%s count=%d", routes[0], routes[1299], len(routes))
	}
	seen := map[netip.Prefix]bool{}
	for _, route := range routes {
		if seen[route] || !pool.Contains(route.Addr()) {
			t.Fatalf("invalid route %s", route)
		}
		seen[route] = true
	}
}

func TestRouteDeltaOnlyTouchesTheDifference(t *testing.T) {
	add, withdraw := routeDelta(1000, 1003)
	if !reflect.DeepEqual(add, []int{1000, 1001, 1002}) || len(withdraw) != 0 {
		t.Fatalf("unexpected add delta: add=%v withdraw=%v", add, withdraw)
	}
	add, withdraw = routeDelta(1003, 1000)
	if len(add) != 0 || !reflect.DeepEqual(withdraw, []int{1002, 1001, 1000}) {
		t.Fatalf("unexpected withdraw delta: add=%v withdraw=%v", add, withdraw)
	}
}

func TestGeneratedPathHasIPv4UnicastFamily(t *testing.T) {
	item, err := path(netip.MustParsePrefix("198.18.0.0/32"), "10.0.0.11")
	if err != nil {
		t.Fatal(err)
	}
	if item.Family.String() != "ipv4-unicast" {
		t.Fatalf("family = %s, want ipv4-unicast", item.Family)
	}
}

func setValidEnvironment(t *testing.T) {
	t.Helper()
	values := map[string]string{
		"LOCAL_AS": "65101", "PEER_AS": "65000", "ROUTER_ID": "10.255.1.1",
		"LOCAL_ADDRESS": "10.0.0.11/31", "PEER_ADDRESS": "10.0.0.10",
		"PREFIX_POOL": "198.18.0.0/20", "BASE_PREFIXES": "1000", "AMPLITUDE": "300",
		"PERIOD_SECONDS": "600", "STEP_SECONDS": "30", "PHASE_SLOT": "0",
	}
	for name, value := range values {
		t.Setenv(name, value)
	}
}

func TestConfigRejectsInvalidBoundsAndPhase(t *testing.T) {
	setValidEnvironment(t)
	if _, err := loadConfig(); err != nil {
		t.Fatalf("valid config rejected: %v", err)
	}
	for name, value := range map[string]string{
		"AMPLITUDE": "1000", "PHASE_SLOT": "8", "PREFIX_POOL": "198.18.0.0/31",
	} {
		t.Run(name, func(t *testing.T) {
			setValidEnvironment(t)
			t.Setenv(name, value)
			if _, err := loadConfig(); err == nil {
				t.Fatalf("%s=%s was accepted", name, value)
			}
		})
	}
}
