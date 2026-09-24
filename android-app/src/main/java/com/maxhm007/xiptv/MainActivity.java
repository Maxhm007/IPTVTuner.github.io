package com.maxhm007.xiptv;

import android.graphics.Color;
import android.os.Bundle;
import android.text.Editable;
import android.text.TextWatcher;
import android.view.Gravity;
import android.view.View;
import android.view.ViewGroup;
import android.widget.ArrayAdapter;
import android.widget.EditText;
import android.widget.LinearLayout;
import android.widget.ListView;
import android.widget.TextView;

import androidx.annotation.NonNull;
import androidx.appcompat.app.AppCompatActivity;
import androidx.media3.common.MediaItem;
import androidx.media3.common.PlaybackException;
import androidx.media3.common.Player;
import androidx.media3.exoplayer.ExoPlayer;
import androidx.media3.ui.PlayerView;

import java.io.BufferedReader;
import java.io.InputStreamReader;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

public final class MainActivity extends AppCompatActivity {
    private static final String PLAYLIST_URL =
            "https://raw.githubusercontent.com/Maxhm007/IPTVTuner.github.io/gh-pages/IPTV-V008.m3u";
    private static final int BG = Color.rgb(8, 13, 24);
    private static final int PANEL = Color.rgb(16, 24, 39);
    private static final int TEXT = Color.rgb(243, 247, 255);
    private static final int MUTED = Color.rgb(156, 171, 192);
    private static final int ACCENT = Color.rgb(39, 211, 162);

    private final ExecutorService executor = Executors.newSingleThreadExecutor();
    private final List<Channel> allChannels = new ArrayList<>();
    private final List<Channel> visibleChannels = new ArrayList<>();
    private ArrayAdapter<Channel> adapter;
    private ExoPlayer player;
    private TextView status;
    private TextView count;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        player = new ExoPlayer.Builder(this).build();
        setContentView(buildUi());
        loadPlaylist();
    }

    private View buildUi() {
        boolean wide = getResources().getConfiguration().screenWidthDp >= 700;
        LinearLayout root = panel(LinearLayout.VERTICAL);
        root.setBackgroundColor(BG);

        LinearLayout header = panel(LinearLayout.HORIZONTAL);
        header.setPadding(dp(20), dp(12), dp(20), dp(12));
        TextView brand = label("XIPTV", 22, TEXT);
        brand.setTypeface(null, android.graphics.Typeface.BOLD);
        count = label("Loading playlist…", 14, MUTED);
        count.setGravity(Gravity.END | Gravity.CENTER_VERTICAL);
        header.addView(brand, new LinearLayout.LayoutParams(0, dp(48), 1));
        header.addView(count, new LinearLayout.LayoutParams(ViewGroup.LayoutParams.WRAP_CONTENT, dp(48)));
        root.addView(header, new LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT));

        LinearLayout content = panel(wide ? LinearLayout.HORIZONTAL : LinearLayout.VERTICAL);
        content.setPadding(dp(12), 0, dp(12), dp(12));
        View channelPanel = buildChannelPanel();
        View playerPanel = buildPlayerPanel();
        if (wide) {
            content.addView(channelPanel, new LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.MATCH_PARENT, 0.34f));
            content.addView(playerPanel, new LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.MATCH_PARENT, 0.66f));
        } else {
            content.addView(playerPanel, new LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, 0, 0.48f));
            content.addView(channelPanel, new LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, 0, 0.52f));
        }
        root.addView(content, new LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, 0, 1));
        return root;
    }

    private View buildChannelPanel() {
        LinearLayout panel = panel(LinearLayout.VERTICAL);
        panel.setBackgroundColor(PANEL);
        panel.setPadding(dp(10), dp(10), dp(10), dp(10));

        EditText search = new EditText(this);
        search.setHint("Search channels");
        search.setHintTextColor(MUTED);
        search.setTextColor(TEXT);
        search.setSingleLine(true);
        search.setPadding(dp(14), 0, dp(14), 0);
        panel.addView(search, new LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, dp(52)));

        ListView list = new ListView(this);
        list.setDividerHeight(1);
        list.setBackgroundColor(PANEL);
        adapter = new ArrayAdapter<Channel>(this, android.R.layout.simple_list_item_2, android.R.id.text1, visibleChannels) {
            @NonNull
            @Override
            public View getView(int position, View convertView, @NonNull ViewGroup parent) {
                View row = super.getView(position, convertView, parent);
                TextView title = row.findViewById(android.R.id.text1);
                TextView subtitle = row.findViewById(android.R.id.text2);
                Channel channel = getItem(position);
                title.setText(channel == null ? "" : channel.name);
                subtitle.setText(channel == null ? "" : channel.group + (channel.url.startsWith("http://") ? "  •  HTTP" : ""));
                title.setTextColor(TEXT);
                subtitle.setTextColor(channel != null && channel.url.startsWith("http://") ? Color.rgb(255, 200, 87) : MUTED);
                row.setPadding(dp(12), dp(8), dp(12), dp(8));
                row.setBackgroundColor(PANEL);
                return row;
            }
        };
        list.setAdapter(adapter);
        list.setOnItemClickListener((parent, view, position, id) -> play(visibleChannels.get(position)));
        panel.addView(list, new LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, 0, 1));

        search.addTextChangedListener(new TextWatcher() {
            public void beforeTextChanged(CharSequence s, int start, int count, int after) {}
            public void onTextChanged(CharSequence s, int start, int before, int count) { filter(s.toString()); }
            public void afterTextChanged(Editable s) {}
        });
        return panel;
    }

    private View buildPlayerPanel() {
        LinearLayout panel = panel(LinearLayout.VERTICAL);
        panel.setBackgroundColor(PANEL);
        LinearLayout.LayoutParams margin = new LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, 0, 1);
        margin.setMargins(dp(12), 0, 0, 0);

        PlayerView playerView = new PlayerView(this);
        playerView.setPlayer(player);
        playerView.setKeepScreenOn(true);
        playerView.setBackgroundColor(Color.BLACK);
        panel.addView(playerView, margin);

        status = label("Select a channel.", 15, MUTED);
        status.setPadding(dp(18), dp(14), dp(18), dp(14));
        panel.addView(status, new LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT));

        player.addListener(new Player.Listener() {
            @Override public void onPlaybackStateChanged(int state) {
                if (state == Player.STATE_BUFFERING) setStatus("Buffering…", MUTED);
                if (state == Player.STATE_READY) setStatus("Playing live.", ACCENT);
                if (state == Player.STATE_ENDED) setStatus("Stream ended.", MUTED);
            }

            @Override public void onPlayerError(@NonNull PlaybackException error) {
                setStatus("Playback failed: " + error.getErrorCodeName(), Color.rgb(255, 107, 122));
            }
        });
        return panel;
    }

    private void loadPlaylist() {
        executor.execute(() -> {
            try {
                HttpURLConnection connection = (HttpURLConnection) new URL(PLAYLIST_URL).openConnection();
                connection.setConnectTimeout(12000);
                connection.setReadTimeout(12000);
                connection.setRequestProperty("User-Agent", "XIPTV-Android/1.0");
                StringBuilder text = new StringBuilder();
                try (BufferedReader reader = new BufferedReader(new InputStreamReader(connection.getInputStream(), StandardCharsets.UTF_8))) {
                    for (String line; (line = reader.readLine()) != null;) text.append(line).append('\n');
                }
                List<Channel> parsed = PlaylistParser.parse(text.toString());
                runOnUiThread(() -> {
                    allChannels.clear();
                    allChannels.addAll(parsed);
                    filter("");
                    setStatus("Playlist loaded. Select a channel.", ACCENT);
                });
            } catch (Exception error) {
                runOnUiThread(() -> setStatus("Playlist failed: " + error.getMessage(), Color.rgb(255, 107, 122)));
            }
        });
    }

    private void filter(String query) {
        visibleChannels.clear();
        for (Channel channel : allChannels) if (channel.matches(query)) visibleChannels.add(channel);
        adapter.notifyDataSetChanged();
        count.setText(visibleChannels.size() + " channels");
    }

    private void play(Channel channel) {
        setStatus("Connecting to " + channel.name + "…", MUTED);
        player.setMediaItem(MediaItem.fromUri(channel.url));
        player.prepare();
        player.play();
    }

    private void setStatus(String message, int color) {
        status.setText(message);
        status.setTextColor(color);
    }

    private LinearLayout panel(int orientation) {
        LinearLayout layout = new LinearLayout(this);
        layout.setOrientation(orientation);
        return layout;
    }

    private TextView label(String text, int sp, int color) {
        TextView view = new TextView(this);
        view.setText(text);
        view.setTextSize(sp);
        view.setTextColor(color);
        view.setGravity(Gravity.CENTER_VERTICAL);
        return view;
    }

    private int dp(int value) {
        return Math.round(value * getResources().getDisplayMetrics().density);
    }

    @Override
    protected void onDestroy() {
        player.release();
        executor.shutdownNow();
        super.onDestroy();
    }
}

