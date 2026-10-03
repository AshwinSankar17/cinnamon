import { FlatList, RefreshControl, Text, View } from 'react-native';
import { ClusterChip, ConnectionBanner } from '@/components/ConnectionBanner';
import { EmptyNote, Mono, TabHeader } from '@/components/ui';
import { api } from '@/src/api';
import { useRemote } from '@/src/hooks';
import { colors, type } from '@/src/theme';

export default function ReposScreen() {
  const { data, error, loading, reload } = useRemote(() => api.repos());

  return (
    <View style={{ flex: 1, backgroundColor: colors.ground }}>
      <TabHeader title="Repos" right={<ClusterChip />} />
      <ConnectionBanner />
      <FlatList
        data={data ?? []}
        keyExtractor={(r) => r.id}
        contentContainerStyle={{ paddingHorizontal: 16, paddingBottom: 24, gap: 8 }}
        refreshControl={<RefreshControl refreshing={loading && !!data} onRefresh={reload} tintColor={colors.accent} />}
        ListEmptyComponent={<EmptyNote>{error ?? (data ? 'No repos registered with Brain.' : 'Loading…')}</EmptyNote>}
        renderItem={({ item: r }) => (
          <View
            style={{
              backgroundColor: colors.surface,
              borderWidth: 1,
              borderColor: colors.hairline,
              borderRadius: 14,
              paddingVertical: 10,
              paddingHorizontal: 14,
              minHeight: 64,
              justifyContent: 'center',
              gap: 2,
            }}
          >
            <Text style={type.rowTitle} numberOfLines={1}>
              {r.name ?? r.id}
            </Text>
            <Mono numberOfLines={1}>{[r.path, r.default_branch].filter(Boolean).join(' · ') || r.id}</Mono>
          </View>
        )}
      />
    </View>
  );
}
