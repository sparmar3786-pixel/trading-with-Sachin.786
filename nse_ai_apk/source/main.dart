import 'dart:async';
import 'dart:convert';
import 'package:flutter/material.dart';
import 'package:http/http.dart' as http;
import 'package:shared_preferences/shared_preferences.dart';

void main() => runApp(const App());

class App extends StatelessWidget {
  const App({super.key});

  @override
  Widget build(BuildContext context) => MaterialApp(
        debugShowCheckedModeBanner: false,
        title: 'NSE AI OI Terminal',
        theme: ThemeData.dark(useMaterial3: true),
        home: const Terminal(),
      );
}

class Terminal extends StatefulWidget {
  const Terminal({super.key});

  @override
  State<Terminal> createState() => _TerminalState();
}

class _TerminalState extends State<Terminal> {
  String url = 'http://192.168.1.10:8000';
  String token = 'change-me';
  Map<String, dynamic>? data;
  String? error;
  Timer? timer;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    final p = await SharedPreferences.getInstance();
    if (!mounted) return;
    setState(() {
      url = p.getString('url') ?? url;
      token = p.getString('token') ?? token;
    });
    timer = Timer.periodic(const Duration(seconds: 3), (_) => fetchSignal());
    fetchSignal();
  }

  @override
  void dispose() {
    timer?.cancel();
    super.dispose();
  }

  String _baseUrl(String value) =>
      value.trim().replaceFirst(RegExp(r'/$'), '');

  Future<void> fetchSignal() async {
    try {
      final r = await http.get(
        Uri.parse('${_baseUrl(url)}/signal'),
        headers: {'x-token': token},
      ).timeout(const Duration(seconds: 7));
      final body = jsonDecode(r.body);
      if (!mounted) return;
      setState(() {
        data = body is Map<String, dynamic>
            ? body
            : {'error': 'Invalid server response'};
        error = r.statusCode >= 400 ? 'HTTP ${r.statusCode}' : null;
      });
    } catch (e) {
      if (!mounted) return;
      setState(() => error = e.toString());
    }
  }

  Color signalColor(String action) {
    if (action == 'BUY_CE') return Colors.greenAccent;
    if (action == 'BUY_PE') return Colors.redAccent;
    if (action == 'EXIT') return Colors.orangeAccent;
    return Colors.white70;
  }

  Widget metric(String label, dynamic value) => Padding(
        padding: const EdgeInsets.symmetric(vertical: 4),
        child: Row(
          mainAxisAlignment: MainAxisAlignment.spaceBetween,
          children: [
            Text(label, style: const TextStyle(color: Colors.white60)),
            Flexible(
              child: Text(
                '$value',
                textAlign: TextAlign.right,
                style: const TextStyle(fontWeight: FontWeight.bold),
              ),
            ),
          ],
        ),
      );

  Future<void> settings() async {
    final u = TextEditingController(text: url);
    final k = TextEditingController(text: token);
    await showDialog(
      context: context,
      builder: (_) => AlertDialog(
        title: const Text('Backend settings'),
        content: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            TextField(
              controller: u,
              decoration: const InputDecoration(labelText: 'Backend URL'),
            ),
            TextField(
              controller: k,
              decoration: const InputDecoration(labelText: 'API token'),
            ),
          ],
        ),
        actions: [
          TextButton(
            onPressed: () async {
              final newUrl = _baseUrl(u.text);
              final newToken = k.text.trim();
              final p = await SharedPreferences.getInstance();
              await p.setString('url', newUrl);
              await p.setString('token', newToken);
              if (!mounted) return;
              setState(() {
                url = newUrl;
                token = newToken;
              });
              Navigator.pop(context);
              fetchSignal();
            },
            child: const Text('Save'),
          ),
        ],
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    final d = data ?? <String, dynamic>{};
    final action = (d?['action'] ?? 'WAIT').toString();
    final nseValue = d?['nse'];
    final nse = nseValue is Map
        ? Map<String, dynamic>.from(nseValue)
        : null;

    return Scaffold(
      appBar: AppBar(
        title: Text((d?['symbol'] ?? 'NSE AI OI').toString()),
        actions: [
          IconButton(
            onPressed: settings,
            icon: const Icon(Icons.settings),
          ),
        ],
      ),
      body: ListView(
        padding: const EdgeInsets.all(16),
        children: [
          if (error != null)
            Text(
              'Connection: $error',
              style: const TextStyle(color: Colors.redAccent),
            ),
          if (d?['market_open'] == false)
            const Padding(
              padding: EdgeInsets.only(bottom: 8),
              child: Text(
                'Market closed',
                style: TextStyle(color: Colors.amber),
              ),
            ),
          Card(
            child: Padding(
              padding: const EdgeInsets.all(16),
              child: Column(
                children: [
                  Text(
                    action.replaceAll('_', ' '),
                    style: TextStyle(
                      fontSize: 32,
                      fontWeight: FontWeight.bold,
                      color: signalColor(action),
                    ),
                  ),
                  const SizedBox(height: 10),
                  if (d?['spot'] != null) metric('Spot', d['spot']),
                  if (d?['strike'] != null)
                    metric(
                      'Strike',
                      '${d['strike']} ${d['type'] ?? ''}',
                    ),
                  if (d?['entry'] != null) metric('Entry', d['entry']),
                  if (d?['ltp'] != null) metric('LTP', d['ltp']),
                  if (d?['sl'] != null) metric('Stop loss', d['sl']),
                  if (d?['target'] != null) metric('Target', d['target']),
                  if (d?['score'] != null) metric('Angel score', d['score']),
                  if (d?['ai_confidence'] != null)
                    metric('AI confidence', d['ai_confidence']),
                ],
              ),
            ),
          ),
          if (nse != null)
            Card(
              child: Padding(
                padding: const EdgeInsets.all(16),
                child: Column(
                  children: [
                    const Align(
                      alignment: Alignment.centerLeft,
                      child: Text(
                        'NSE AI Trend',
                        style: TextStyle(
                          fontSize: 18,
                          fontWeight: FontWeight.bold,
                        ),
                      ),
                    ),
                    metric('Trend', nse['trend']),
                    metric('P(up)', nse['p_up']),
                    metric('Source', nse['source']),
                    metric('PCR (OI)', nse['pcr']),
                    metric('Support', nse['support']),
                    metric('Resistance', nse['resistance']),
                    metric('Max pain', nse['max_pain']),
                  ],
                ),
              ),
            ),
          if (d?['nse_error'] != null)
            Text(
              'NSE: ${d['nse_error']}',
              style: const TextStyle(color: Colors.orangeAccent),
            ),
          const SizedBox(height: 8),
          const Text(
            'Reasons',
            style: TextStyle(fontWeight: FontWeight.bold),
          ),
          ...((d?['reasons'] as List?) ?? const [])
              .map((x) => Text('• $x')),
          const SizedBox(height: 18),
          const Text(
            'Paper signals only. No orders are placed. Profit is not guaranteed.',
            style: TextStyle(color: Colors.white38, fontSize: 12),
          ),
        ],
      ),
    );
  }
}
