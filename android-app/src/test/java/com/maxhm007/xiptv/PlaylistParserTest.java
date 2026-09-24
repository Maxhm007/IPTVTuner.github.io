package com.maxhm007.xiptv;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertTrue;

import org.junit.Test;

import java.util.List;

public final class PlaylistParserTest {
    @Test
    public void parsesHttpAndHttpsAndSkipsInactiveEntries() {
        String playlist = "#EXTM3U\n"
                + "### Bangladesh ###\n"
                + "#EXTINF:-1 group-title=\"Bangladesh\",HTTP Channel\n"
                + "http://example.com/live.m3u8\n"
                + "#SELFHEAL-INACTIVE #EXTINF:-1,Dead Channel\n"
                + "#SELFHEAL-URL http://example.com/dead.m3u8\n"
                + "#EXTINF:-1 group-title=\"News\",HTTPS Channel\n"
                + "https://example.com/news.m3u8\n";

        List<Channel> channels = PlaylistParser.parse(playlist);

        assertEquals(2, channels.size());
        assertEquals("http://example.com/live.m3u8", channels.get(0).url);
        assertEquals("https://example.com/news.m3u8", channels.get(1).url);
        assertTrue(channels.get(0).matches("bangladesh"));
    }
}

