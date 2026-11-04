package org.kidoo.tv

import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test

class ArtworkLayoutTest {
    private fun video(id: String, kind: String, type: String = "", ratio: Double? = null): Video {
        val raw=JSONObject().put("id",id).put("key",id).put("kind",kind).put("mediaType",type).put("title",id)
        if (ratio != null) raw.put("artworkAspectRatio",ratio)
        return Video(raw)
    }

    @Test fun usesArtworkShapeAndFallsBackToContentType() {
        assertTrue(ArtworkLayout.portrait(video("movie","jellyfin","Movie")))
        assertTrue(ArtworkLayout.portrait(video("show","series")))
        assertFalse(ArtworkLayout.portrait(video("episode","jellyfin","Episode")))
        assertFalse(ArtworkLayout.portrait(video("wideMovie","jellyfin","Movie",1.78)))
        assertTrue(ArtworkLayout.portrait(video("posterEpisode","jellyfin","Episode",0.67)))
    }

    @Test fun mixedShelvesKeepEveryItemAndRelativeOrder() {
        val items=listOf(video("a","external"),video("b","series"),video("c","external"),video("d","jellyfin","Movie"))
        val shelves=ArtworkLayout.shelves(items)
        assertEquals(listOf(false,true),shelves.map { it.first })
        assertEquals(listOf("a","c"),shelves[0].second.map { it.title })
        assertEquals(listOf("b","d"),shelves[1].second.map { it.title })
        assertTrue(ArtworkLayout.shelves(emptyList()).isEmpty())
    }
}
