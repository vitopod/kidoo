package org.kidoo.tv

import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test

class VideoTagFilterTest {
    private fun video(topic: String, vararg tags: String) =
        JSONObject().put("topic",topic).put("tags",JSONArray(tags.toList()))

    @Test fun topicsAndTagsAreDeduplicatedAndSortedByAvailableVideos() {
        val videos=listOf(video("Science","space","Space"),video("Stories","space"),video("Nature","bees"))
        val choices=VideoTagFilter.choices(videos)
        assertEquals("space",choices.first().key)
        assertEquals(2,choices.first().count)
        assertEquals(1,choices.count { it.key=="space" })
    }

    @Test fun selectedTagsAreCombinedAndAccentsAreNormalised() {
        val v=video("Nature","abeilles","forêt")
        assertTrue(VideoTagFilter.matches(v,emptySet()))
        assertTrue(VideoTagFilter.matches(v,setOf("nature","foret")))
        assertFalse(VideoTagFilter.matches(v,setOf("nature","space")))
    }

    @Test fun filteringOnlyReturnsItemsInTheSuppliedEligibleList() {
        val allowed=video("Space","moon")
        val notSupplied=video("Space","moon")
        val eligible=listOf(allowed)
        assertEquals(listOf(allowed),eligible.filter { VideoTagFilter.matches(it,setOf("moon")) })
        assertFalse(eligible.any { it===notSupplied })
    }
}
