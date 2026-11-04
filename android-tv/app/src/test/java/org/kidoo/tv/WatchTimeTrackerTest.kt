package org.kidoo.tv

import org.junit.Assert.assertEquals
import org.junit.Test

class WatchTimeTrackerTest {
    @Test fun countsPlaybackButNotPausesOrSeeking() {
        val tracker = WatchTimeTracker()
        assertEquals(0,tracker.observe(0,1000,true))
        assertEquals(5000,tracker.observe(5000,6000,true))
        assertEquals(5000,tracker.observe(70000,6500,true)) // seek forward
        assertEquals(10000,tracker.observe(75000,11500,false))
        assertEquals(10000,tracker.observe(75000,30000,true)) // paused interval
        assertEquals(15000,tracker.observe(80000,35000,true))
        tracker.resetObservation()
        assertEquals(15000,tracker.observe(20000,60000,true)) // reconnect
    }
}
