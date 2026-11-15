package com.redabensaad.nbadraft_scraper.util;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.SerializationFeature;
import com.fasterxml.jackson.databind.node.ArrayNode;

import java.io.File;
import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;

public class DataSaver {
    private static final String DEFAULT_BASE_PATH = "src/main/resources/data/";

    private final ObjectMapper mapper;
    private final Map<String, List<Object>> objectSaves;
    private final String basePath;

    public DataSaver() {
        this(DEFAULT_BASE_PATH);
    }

    /**
     * @param basePath directory containing the JSON datasets; tests can point it
     *                 at a temporary folder while production keeps the default path
     */
    public DataSaver(String basePath) {
        this.mapper = new ObjectMapper().enable(SerializationFeature.INDENT_OUTPUT);
        this.objectSaves = new HashMap<>();
        this.basePath = basePath;
        try {
            Files.createDirectories(Paths.get(basePath));
        } catch (IOException e) {
            System.err.println("Error creating output directory: " + e.getMessage());
        }
    }

    public void addObject(String fileName, Object object) {
        objectSaves.computeIfAbsent(fileName, k -> new ArrayList<>()).add(object);
    }

    public void saveObjects() {
        if (objectSaves.isEmpty()) return;

        objectSaves.forEach((fileName, newList) -> {
            try {
                appendAndPersist(fileName, newList);
            } catch (IOException e) {
                System.err.println("Failed to save " + fileName + ": " + e.getMessage());
            }
        });

        objectSaves.clear();
    }

    /**
     * Appends a single object to {@code fileName} and flushes the file
     * immediately. Used while scraping a team so that players parsed before a
     * mid-team failure are already on disk.
     */
    public synchronized void appendObject(String fileName, Object object) throws IOException {
        appendAndPersist(fileName, List.of(object));
    }

    /**
     * Removes every player record whose {@code teamId} equals {@code teamCode}
     * from {@code players-data.json}. Called before re-scraping a team that is
     * not marked as finished (e.g. a previous attempt failed mid-team), which
     * guarantees a resume run never produces duplicate player records.
     *
     * @return number of stale records removed
     */
    public synchronized int removePlayersByTeam(String fileName, String teamCode) {
        Path path = Paths.get(basePath, fileName);
        if (!Files.exists(path)) return 0;

        try {
            JsonNode root = mapper.readTree(path.toFile());
            if (root == null || !root.isArray()) return 0;

            ArrayNode kept = mapper.createArrayNode();
            int removed = 0;
            for (JsonNode node : root) {
                if (teamCode.equals(node.path("teamId").asText(""))) {
                    removed++;
                } else {
                    kept.add(node);
                }
            }

            if (removed > 0) {
                mapper.writeValue(path.toFile(), kept);
                System.out.printf("Removed %d stale record(s) of team %s from %s\n",
                        removed, teamCode, fileName);
            }
            return removed;
        } catch (IOException e) {
            System.err.println("Failed to clean " + fileName + " for team " + teamCode
                    + ": " + e.getMessage());
            return 0;
        }
    }

    private void appendAndPersist(String fileName, List<Object> newObjects) throws IOException {
        Path path = Paths.get(basePath, fileName);
        List<Object> finalDataToSave = new ArrayList<>();

        if (Files.exists(path)) {
            try {
                // Read as plain objects so the same logic works for both the
                // player records (maps) and team-scraper.json (plain strings).
                // Reading strings with a Map target type fails and would silently
                // discard the previously stored teams.
                List<Object> existingData = mapper.readValue(path.toFile(),
                        mapper.getTypeFactory().constructCollectionType(List.class, Object.class));
                finalDataToSave.addAll(existingData);
            } catch (IOException e) {
                System.out.println("Initializing new file: " + fileName);
            }
        }

        finalDataToSave.addAll(newObjects);

        mapper.writeValue(path.toFile(), finalDataToSave);
        System.out.printf("File updated: %s (%d new, %d total)\n",
                fileName, newObjects.size(), finalDataToSave.size());
    }

    public <T> List<T> loadList(String fileName, Class<T> clazz) {
        File file = new File(basePath + fileName);
        if (!file.exists()) return new ArrayList<>();

        try {
            return mapper.readValue(file,
                    mapper.getTypeFactory().constructCollectionType(List.class, clazz));
        } catch (IOException e) {
            System.err.println("Read error: " + e.getMessage());
            return new ArrayList<>();
        }
    }
}
