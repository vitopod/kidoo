package org.kidoo.tv

import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test

class SearchCatalogTest {
    @Test fun communityResultsAcceptOnlyFamilyMedia() {
        for (kind in listOf("local", "jellyfin", "series")) assertTrue(CommunityCatalog.supports(JSONObject().put("kind",kind)))
        for (kind in listOf("external", "youtube", "", "other")) assertFalse(CommunityCatalog.supports(JSONObject().put("kind",kind)))
        assertFalse(CommunityCatalog.supports(JSONObject()))
    }
    private val video=Video(JSONObject().put("id","1").put("key","jellyfin:1")
        .put("kind","jellyfin").put("title","Forêt enchantée").put("creator","Studio")
        .put("summary","A woodland adventure"))

    @Test fun findsTitleCreatorAndDescriptionIgnoringAccentsAndCase() {
        assertTrue(SearchCatalog.matches(video,"FORET"))
        assertTrue(SearchCatalog.matches(video,"studio"))
        assertTrue(SearchCatalog.matches(video,"woodland"))
        assertFalse(SearchCatalog.matches(video,"ocean"))
    }
}
