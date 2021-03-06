import tensorflow as tf
from src.models.SMC_Transformer.transformer_utils import create_look_ahead_mask
from src.train.train_step_functions import train_step_classic_T
from src.train.train_step_functions import train_step_SMC_T
import time
import os
from src.utils.utils_train import saving_training_history, write_to_csv, restoring_checkpoint


def train_LSTM(model, optimizer, EPOCHS, train_dataset, val_dataset, output_path, checkpoint_path, logger, num_train):
    LSTM_ckpt_path = os.path.join(checkpoint_path, "RNN_Baseline_{}".format(num_train))
    LSTM_ckpt_path = LSTM_ckpt_path + '/' + 'LSTM-{epoch}'

    callbacks = [
        tf.keras.callbacks.ModelCheckpoint(
            filepath=LSTM_ckpt_path,
            monitor='val_loss',
            save_best_only=True,
            save_weights_only=True,
            verbose=0)
    ]
    model.compile(optimizer=optimizer,
                  loss='mse')

    print(model.summary())

    # --- starting the training ... -----------------------------------------------
    start_training = time.time()
    rnn_history = model.fit(train_dataset,
                            epochs=EPOCHS,
                            validation_data=val_dataset,
                            callbacks=callbacks,
                            verbose=2)

    train_loss_history_rnn = rnn_history.history['loss']
    val_loss_history_rnn = rnn_history.history['val_loss']
    keys = ['train_loss', 'val_loss']
    values = [train_loss_history_rnn, val_loss_history_rnn]
    csv_fname = 'rnn_history_{}.csv'.format(num_train)
    dict_hist = dict(zip(keys, values))
    write_to_csv(output_dir=os.path.join(output_path, csv_fname), dic=dict_hist)

    logger.info('Training time for {} epochs: {}'.format(EPOCHS, time.time() - start_training))
    logger.info('training of a RNN Baseline for a timeseries dataset done...')
    logger.info(
        ">>>--------------------------------------------------------------------------------------------------------------------------------------------------------------<<<")


def train_baseline_transformer(transformer, optimizer, EPOCHS, train_dataset, val_dataset, output_path, ckpt_manager,
                               logger, start_epoch, num_train=1):
    # storing the losses & accuracy in a list for each epoch
    average_losses_baseline, val_losses_baseline = [], []
    start_training = time.time()

    if start_epoch > 0:
        if start_epoch >= EPOCHS:
            print("adding {} more epochs to existing training".format(EPOCHS))
            start_epoch = 0
        else:
            logger.info("starting training after checkpoint restoring from epoch {}".format(start_epoch))

    for epoch in range(start_epoch, EPOCHS):
        start = time.time()
        logger.info("Epoch {}/{}".format(epoch + 1, EPOCHS))

        sum_train_loss, sum_val_loss = 0, 0

        for (batch, (inp, tar)) in enumerate(train_dataset):
            train_loss_batch = train_step_classic_T(inputs=inp,
                                                    targets=tar,
                                                    transformer=transformer,
                                                    optimizer=optimizer)
            sum_train_loss += train_loss_batch

            if batch == 0 and epoch == 0:
                print('baseline transformer summary', transformer.summary())

        avg_train_loss = sum_train_loss / (batch + 1)

        for batch_val, (inp, tar) in enumerate(val_dataset):
            seq_len = tf.shape(inp)[-2]
            predictions_val, attn_weights_val = transformer(inputs=inp,
                                                            training=False,
                                                            mask=create_look_ahead_mask(seq_len))
            val_loss_batch = tf.keras.losses.MSE(tar, predictions_val)
            val_loss_batch = tf.reduce_mean(val_loss_batch)
            sum_val_loss += val_loss_batch

        avg_val_loss = sum_val_loss / (batch_val + 1)

        logger.info('train loss: {:5.3f} - val loss: {:5.3f}'.format(avg_train_loss.numpy(), avg_val_loss.numpy()))
        average_losses_baseline.append(avg_train_loss.numpy())
        val_losses_baseline.append(avg_val_loss.numpy())

        ckpt_manager.save()
        logger.info('Time taken for 1 epoch: {:10.1f} secs\n'.format(time.time() - start))

    logger.info('total training time for {:10.1f} epochs:{}'.format(EPOCHS, time.time() - start_training))

    # storing history of losses and accuracies in a csv file
    keys = ['train loss', 'val loss']
    values = [average_losses_baseline, val_losses_baseline]
    csv_fname = 'baseline_history_{}.csv'.format(num_train)

    saving_training_history(keys=keys,
                            values=values,
                            output_path=output_path,
                            csv_fname=csv_fname,
                            logger=logger,
                            start_epoch=start_epoch)

    logger.info('training of a classic Transformer for a time-series dataset done...')
    logger.info(
        ">>>-------------------------------------------------------------------------------------------------------------------------------------------------------------<<<")


def train_SMC_transformer(smc_transformer, optimizer, EPOCHS, train_dataset, val_dataset, output_path, ckpt_manager, logger,
                          start_epoch, num_train):

    losses_history = {"train_loss":[], "train_mse_metric":[], "val_loss":[], "val_mse_metric": []}

    # check the pass forward.
    for input_example_batch, target_example_batch in train_dataset.take(1):
        (temp_preds, temp_preds_resampl), _, _ = smc_transformer(inputs=input_example_batch,
                                                                 targets=target_example_batch)
        logger.info("predictions shape: {}".format(temp_preds.shape))
        print('first element and first dim of predictions - t0', temp_preds[0, :, 0, 0].numpy())
        print('first element and first dim of predictions resampled - t0', temp_preds_resampl[0, :, 0, 0].numpy())
        print('first element and first dim of predictions - t10', temp_preds[0, :, 10, 0].numpy())
        print('first element and first dim of predictions resampled - t10', temp_preds_resampl[0, :, 10, 0].numpy())
        print('first element and first dim of predictions - t24', temp_preds[0, :, -1, 0].numpy())
        print('first element and first dim of predictions resampled - t24', temp_preds_resampl[0, :, -1, 0].numpy())

    if start_epoch > 0:
        if start_epoch > EPOCHS:
            logger.info("adding {} more epochs to existing training".format(EPOCHS))
            start_epoch = 0
        else:
            logger.info("starting training after checkpoint restoring from epoch {}".format(start_epoch))

    it = 0
    for epoch in range(start_epoch, EPOCHS):
        start = time.time()
        logger.info('Epoch {}/{}'.format(epoch + 1, EPOCHS))

        if smc_transformer.cell.noise:
            train_loss, val_loss = [0. for _ in range(2)], [0. for _ in range(2)]
        else:
            train_loss, val_loss = [0.], [0.]

        for batch, (inp, tar) in enumerate(train_dataset):
            it += 1
            train_loss_batch, train_metric_avg_pred = train_step_SMC_T(inputs=inp, targets=tar,
                                                                       smc_transformer=smc_transformer,
                                                                       optimizer=optimizer,
                                                                       it=it)
            train_loss[0] += train_loss_batch

            if smc_transformer.cell.noise:
                print('sigma_obs:{} - sigma_k:{} - sigma_q: {} - sigma_v: {} - sigma_z: {}'.format(
                    smc_transformer.cell.Sigma_obs,
                    smc_transformer.cell.attention_smc.sigma_k,
                    smc_transformer.cell.attention_smc.sigma_q,
                    smc_transformer.cell.attention_smc.sigma_v,
                    smc_transformer.cell.attention_smc.sigma_z))
                train_loss[1] += train_metric_avg_pred

        for batch_val, (inp, tar) in enumerate(val_dataset):
            (preds_val, preds_val_resampl), _, _ = smc_transformer(inputs=inp, targets=tar)  # shape (B,1,S,F_y)
            if smc_transformer.cell.noise:
                tar_tiled = tf.tile(tar, multiples=[1, smc_transformer.cell.num_particles, 1, 1])
                val_loss_batch = smc_transformer.compute_SMC_loss(targets=tar_tiled, predictions=preds_val_resampl)
                val_metric_avg_pred = tf.keras.losses.MSE(tar, tf.reduce_mean(preds_val, axis=1, keepdims=True))
                val_metric_avg_pred = tf.reduce_mean(val_metric_avg_pred)
                val_loss[0] += val_loss_batch
                val_loss[1] += val_metric_avg_pred
            else:
                val_loss_batch = tf.keras.losses.MSE(tar, preds_val_resampl)  # (B,1,S)
                val_loss_batch = tf.reduce_mean(val_loss_batch)  # mean over all dims.
                val_loss[0] += val_loss_batch

        train_loss, val_loss = [i / (batch + 1) for i in train_loss], [i / (batch_val + 1) for i in val_loss]
        logger.info('train loss: {} - val loss: {}'.format(train_loss[0].numpy(), val_loss[0].numpy()))
        losses_history["train_loss"].append(train_loss[0].numpy())
        losses_history["val_loss"].append(val_loss[0].numpy())
        if smc_transformer.cell.noise:
            logger.info(
                'train mse metric from avg particule: {} - val mse metric from avg particule: {}'.format(
                    train_loss[1].numpy(), val_loss[1].numpy()))
            losses_history["train_mse_metric"].append(train_loss[1].numpy())
            losses_history["val_mse_metric"].append(val_loss[1].numpy())
            logger.info('sigma_obs:{} - sigma_k:{} - sigma_q: {} - sigma_v: {} - sigma_z: {}'.format(
                smc_transformer.cell.Sigma_obs,
                smc_transformer.cell.attention_smc.sigma_k,
                smc_transformer.cell.attention_smc.sigma_q,
                smc_transformer.cell.attention_smc.sigma_v,
                smc_transformer.cell.attention_smc.sigma_z))

        ckpt_manager.save()
        logger.info('Time taken for 1 epoch: {} secs'.format(time.time() - start))

    csv_fname = 'smc_t_history_{}.csv'.format(num_train)

    saving_training_history(keys=list(losses_history.keys()),
                            values=list(losses_history.values()),
                            output_path=output_path,
                            csv_fname=csv_fname,
                            logger=logger,
                            start_epoch=start_epoch)
    logger.info('total training time for {} epochs:{}'.format(EPOCHS, time.time() - start))
