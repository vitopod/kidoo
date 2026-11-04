package org.kidoo.tv

import org.junit.Assert.*
import org.junit.Test
import org.junit.Assume.assumeTrue
import okhttp3.Request

class PolicyTest {
    @Test
    fun playbackTimeShowsMinutesAndHours() {
        assertEquals("0:07", VideoTime.clock(7000))
        assertEquals("12:34", VideoTime.clock(754000))
        assertEquals("1:02:03", VideoTime.clock(3723000))
        assertEquals("", VideoTime.durationLabel(0.0))
    }
    @Test
    fun localRouteRequiresHttpsAndPrivateIpv4() {
        assertNull(localRoute("https://kidoo.example", ""))
        val route = localRoute("https://kidoo.example:8765", "192.168.5.10")!!
        assertEquals("kidoo.example", route.first)
        assertEquals("192.168.5.10", route.second.hostAddress)
        for ((base, address) in listOf("http://kidoo.example" to "192.168.5.10", "https://kidoo.example" to "8.8.8.8", "https://kidoo.example" to "192.168.1.999", "https://kidoo.example" to "example.com")) {
            try { localRoute(base, address); fail("Invalid local route accepted") }
            catch (_: IllegalArgumentException) {}
        }
    }

    @Test
    fun localRoutePreservesCertificateVerification() {
        val endpoint = System.getenv("KIDOO_TEST_HTTPS_URL") ?: ""
        val address = System.getenv("KIDOO_TEST_LOCAL_ADDRESS") ?: ""
        assumeTrue(endpoint.isNotBlank() && address.isNotBlank())
        val client = buildHttpClient(endpoint, address)
        client.newCall(Request.Builder().url(endpoint + "/api/profiles").build()).execute().use {
            assertEquals(401, it.code)
        }
        val port = java.net.URI(endpoint).port
        val wrongHost = "https://wrong-host.invalid:$port"
        try {
            buildHttpClient(wrongHost, address).newCall(Request.Builder().url(wrongHost + "/api/profiles").build()).execute().close()
            fail("Certificate with the wrong hostname was accepted")
        } catch (_: javax.net.ssl.SSLPeerUnverifiedException) {}
    }
    @Test
    fun normalisesOnlyServerRoots() {
        assertEquals(
            "https://kidoo.example",
            ConnectionPolicy.normalise(" https://kidoo.example/ "),
        )
        assertEquals(
            "http://kidoo.local:8765",
            ConnectionPolicy.normalise("http://kidoo.local:8765"),
        )
    }

    @Test
    fun rejectsCredentialsAndNonRoot() {
        for (v in
            listOf(
                "file:///tmp/",
                "ftp://server/",
                "http://user:pass@example.org/",
                "http://example.org/login",
                "http://example.org/?key=secret",
                "not a server",
            )) {
            try {
                ConnectionPolicy.normalise(v)
                fail(v)
            } catch (_: IllegalArgumentException) {}
        }
    }

    @Test
    fun externalLinksAreRestricted() {
        assertTrue(ConnectionPolicy.external("https://www.youtube.com/watch?v=example"))
        assertFalse(ConnectionPolicy.external("intent:danger"))
        assertFalse(ConnectionPolicy.external("http://user:pass@example.org"))
    }

    @Test
    fun resumeAndCompletion() {
        assertEquals(0L, ProgressPolicy.resume(900, 1000, true))
        assertEquals(1000L, ProgressPolicy.resume(1500, 1000, false))
        assertEquals(0L, ProgressPolicy.resume(-1, 1000, false))
        assertFalse(ProgressPolicy.completed(949, 1000, false))
        assertTrue(ProgressPolicy.completed(950, 1000, false))
        assertTrue(ProgressPolicy.completed(5, 0, true))
        assertFalse(ProgressPolicy.completed(1, 0, false))
    }
}
