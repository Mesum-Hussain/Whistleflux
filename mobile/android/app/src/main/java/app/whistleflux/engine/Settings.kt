package app.whistleflux.engine

import android.content.Context
import androidx.core.content.edit

/** User settings, shared by the setup app and the keyboard (same process, same prefs file). */
data class Settings(
    /** Rule-based clean-up on the phone: fillers, stutters, "scratch that", "new line". */
    val basicCleanup: Boolean = true,
    /** Smart clean-up with the Gemini API (needs [apiKey]). */
    val smartCleanup: Boolean = true,
    val apiKey: String = "",
    val model: String = DEFAULT_MODEL,
    /** Extra rules for the smart clean-up, e.g. names it should spell correctly. */
    val instructions: String = "",
) {
    /** A Gemini model the user can pick in the setup screen. */
    data class Model(val id: String, val label: String, val note: String)

    companion object {
        // Choices in the settings screen; the first is the default.
        val MODELS = listOf(
            Model("gemini-3.5-flash-lite", "Gemini 3.5 Flash-Lite", "Fast, with a large free daily limit"),
            Model("gemini-3.1-flash-lite", "Gemini 3.1 Flash-Lite", "Fast, with a large free daily limit"),
            Model("gemini-3.5-flash", "Gemini 3.5 Flash", "Stronger, but only 20 free uses a day"),
            Model("gemini-3.8-flash", "Gemini 3.8 Flash", "Strongest and slower, only 20 free uses a day"),
        )
        val DEFAULT_MODEL = MODELS.first().id

        /**
         * Tried in order when the chosen model is out of free quota, busy or gone, so
         * dictation still gets cleaned up.
         */
        val FALLBACK_MODELS = listOf("gemini-3.5-flash-lite", "gemini-3.1-flash-lite")

        private const val PREFS = "whistleflux"

        fun load(context: Context): Settings {
            val p = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
            return Settings(
                basicCleanup = p.getBoolean("basicCleanup", true),
                smartCleanup = p.getBoolean("smartCleanup", true),
                apiKey = p.getString("apiKey", "") ?: "",
                // Older versions let people type any name; keep only names from the list.
                model = p.getString("model", null)?.takeIf { id -> MODELS.any { it.id == id } } ?: DEFAULT_MODEL,
                instructions = p.getString("instructions", "") ?: "",
            )
        }

        fun save(context: Context, s: Settings) {
            context.getSharedPreferences(PREFS, Context.MODE_PRIVATE).edit {
                putBoolean("basicCleanup", s.basicCleanup)
                putBoolean("smartCleanup", s.smartCleanup)
                putString("apiKey", s.apiKey)
                putString("model", s.model)
                putString("instructions", s.instructions)
                remove("formatEnabled") // replaced by smartCleanup
            }
        }
    }
}
