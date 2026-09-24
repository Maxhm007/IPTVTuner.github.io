package com.maxhm007.xiptv;

import java.util.ArrayList;
import java.util.List;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

public final class PlaylistParser {
    private static final Pattern GROUP = Pattern.compile("group-title=\"([^\"]*)\"", Pattern.CASE_INSENSITIVE);
    private static final Pattern SECTION = Pattern.compile("^#{3,}\\s*(.*?)\\s*#{3,}$");

    private PlaylistParser() {}

    public static List<Channel> parse(String text) {
        List<Channel> channels = new ArrayList<>();
        String pendingName = null;
        String pendingGroup = null;
        String section = "Other";

        for (String raw : text.split("\\r?\\n")) {
            String line = raw.trim();
            if (line.isEmpty()) continue;

            Matcher sectionMatch = SECTION.matcher(line);
            if (sectionMatch.matches()) {
                section = sectionMatch.group(1).replaceAll("\\s*/\\s*", " / ").trim();
                continue;
            }

            if (line.startsWith("#SELFHEAL-INACTIVE") || line.startsWith("#SELFHEAL-URL")) {
                pendingName = null;
                pendingGroup = null;
                continue;
            }

            if (line.startsWith("#EXTINF:")) {
                int comma = line.lastIndexOf(',');
                String attributes = comma >= 0 ? line.substring(0, comma) : line;
                pendingName = comma >= 0 ? line.substring(comma + 1).trim() : "Unnamed Channel";
                Matcher groupMatch = GROUP.matcher(attributes);
                pendingGroup = groupMatch.find() && !groupMatch.group(1).isEmpty()
                        ? groupMatch.group(1)
                        : section;
                continue;
            }

            if (pendingName != null && (line.startsWith("http://") || line.startsWith("https://"))) {
                channels.add(new Channel(pendingName, pendingGroup, line));
                pendingName = null;
                pendingGroup = null;
            }
        }
        return channels;
    }
}

