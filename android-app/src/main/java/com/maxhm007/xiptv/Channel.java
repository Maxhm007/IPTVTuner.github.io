package com.maxhm007.xiptv;

import java.util.Locale;

public final class Channel {
    public final String name;
    public final String group;
    public final String url;

    public Channel(String name, String group, String url) {
        this.name = name;
        this.group = group;
        this.url = url;
    }

    public boolean matches(String query) {
        String needle = query == null ? "" : query.trim().toLowerCase(Locale.ROOT);
        return needle.isEmpty()
                || name.toLowerCase(Locale.ROOT).contains(needle)
                || group.toLowerCase(Locale.ROOT).contains(needle);
    }

    @Override
    public String toString() {
        return name + "\n" + group;
    }
}

