"""Show text continuation and an actual SQLite suspend/resume round trip."""
import json,tempfile
from pathlib import Path
from turnledger import Ledger,SQLiteStore,TurnCursor
from turnledger.adapters.text_stream import TextBargeInSimulator
comparison=TextBargeInSimulator(['A','B','C','D'],interrupt_at=2,user_utterance='pause',turn_id='demo').contrast()
with tempfile.TemporaryDirectory(prefix='turnledger-demo-') as directory:
    db=str(Path(directory)/'ledger.db')
    first_store=SQLiteStore(db);saved=Ledger(first_store);saved.report('durable',cursor=TurnCursor(token_index=2),payload={'remaining':'CD'})
    saved.suspend('durable')
    first_store.close()
    second_store=SQLiteStore(db);reopened=Ledger(second_store);resumed=reopened.resume('durable')
    result={'continue_output':''.join(comparison['ledger_mode']['emitted']),
            'restart_comparison':''.join(comparison['restart_mode']['emitted']),
            'restored_cursor':resumed.cursor.token_index,'restored_payload':resumed.payload,
            'history_phases':[e.phase for e in reopened.history('durable')],
            'resumable_after_resume':reopened.is_resumable('durable')}
    second_store.close()
    print(json.dumps(result,indent=2))
    assert result['continue_output']=='ABCD' and result['restored_cursor']==2
