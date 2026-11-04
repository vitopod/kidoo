package org.kidoo.tv

import java.net.InetAddress
import java.net.ServerSocket
import java.util.concurrent.atomic.AtomicInteger
import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test

class ApiTest {
    private fun fixture(action: (KidooApi, AtomicInteger, AtomicInteger) -> Unit) {
        val logins = AtomicInteger()
        val attempts = AtomicInteger()
        val server = ServerSocket(0, 10, InetAddress.getLoopbackAddress())
        val worker = Thread {
            while (!server.isClosed) try {
                server.accept().use { socket ->
                    socket.soTimeout = 5000
                    val reader = socket.getInputStream().bufferedReader()
                    val path = reader.readLine().split(' ')[1]
                    val headers = mutableMapOf<String, String>()
                    while (true) {
                        val line = reader.readLine() ?: break
                        if (line.isEmpty()) break
                        headers[line.substringBefore(':').lowercase()] =
                            line.substringAfter(':').trim()
                    }
                    val chars = CharArray(headers["content-length"]?.toInt() ?: 0)
                    var offset = 0
                    while (offset < chars.size) {
                        val n = reader.read(chars, offset, chars.size - offset)
                        if (n < 0) break
                        offset += n
                    }
                    var status = 200
                    var cookie = ""
                    val body =
                        when (path) {
                            "/api/tv/info" -> """{"product":"Kidoo","apiVersion":1}"""
                            "/api/login" -> {
                                logins.incrementAndGet()
                                val data = JSONObject(String(chars))
                                if (data.getString("password") != "fixture-password") {
                                    status = 401
                                    "{}"
                                } else {
                                    cookie =
                                        "Set-Cookie: kidoo_session=fixture; HttpOnly; Path=/\r\n"
                                    "{}"
                                }
                            }
                            "/api/profiles" -> {
                                attempts.incrementAndGet()
                                if (headers["cookie"] != "kidoo_session=fixture") {
                                    status = 401
                                    "{}"
                                } else "[]"
                            }
                            "/expire" -> {
                                if (attempts.incrementAndGet() == 1) {
                                    status = 401
                                    "{}"
                                } else "{}"
                            }
                            "/denied" -> {
                                attempts.incrementAndGet()
                                status = 401
                                "{}"
                            }
                            else -> {
                                status = 503
                                "{}"
                            }
                        }
                    val bytes = body.toByteArray()
                    val head =
                        "HTTP/1.1 $status Response\r\nContent-Length: ${bytes.size}\r\nContent-Type: application/json\r\n${cookie}Connection: close\r\n\r\n"
                    socket.getOutputStream().apply {
                        write(head.toByteArray())
                        write(bytes)
                        flush()
                    }
                }
            } catch (_: java.io.IOException) {
                if (server.isClosed) break
            }
        }
            .apply {
                isDaemon = true
                start()
            }
        try {
            action(
                KidooApi("http://127.0.0.1:" + server.localPort, "fixture-password"),
                logins,
                attempts,
            )
        } finally {
            server.close()
            worker.join(1000)
        }
    }

    @Test
    fun authenticatesAndReusesCookie() {
        fixture { api, logins, _ ->
            api.signIn()
            assertEquals("[]", api.get("/api/profiles"))
            assertEquals(1, logins.get())
        }
    }

    @Test
    fun expiresThenReauthenticatesOnce() {
        fixture { api, logins, attempts ->
            api.signIn()
            assertEquals("{}", api.get("/expire"))
            assertEquals(2, logins.get())
            assertEquals(2, attempts.get())
        }
    }

    @Test
    fun repeatedUnauthorizedDoesNotLoop() {
        fixture { api, logins, attempts ->
            api.signIn()
            try {
                api.get("/denied")
                fail()
            } catch (e: ApiFailure) {
                assertEquals("credentials", e.category)
            }
            assertEquals(2, logins.get())
            assertEquals(2, attempts.get())
        }
    }

    @Test
    fun preservesServerFailureDistinction() {
        fixture { api, _, _ ->
            api.signIn()
            try {
                api.get("/failure")
                fail()
            } catch (e: ApiFailure) {
                assertEquals("server", e.category)
                assertEquals(503, e.status)
            }
        }
    }
}
